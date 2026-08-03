#!/usr/bin/env python3
"""
Import the City of Sacramento's official transportation projects as votes on a
City Edit map.

Sacramento twin of tools/nyc_proposals/import_to_map.py — same two-phase
plan→cast shape, same public-HTTP-only contract (the server does all snapping,
so a plan casts identically against local and prod), same synthetic-voter
convention. Deliberately a separate script rather than a shared library: the
NYC importer backs a live weekly Cloud Run job, and the two sources differ in
every part that isn't the cast mechanics.

What differs from NYC:
  - The source index is curated (see fetch_projects.py), so there is no
    two-sided junk filter — just a small blacklist for the non-street projects
    the Public Works index happens to carry (broadband, etc.).
  - Sacramento states project limits in plain prose ("located on Folsom
    Boulevard, between 47th Street and 67th Street"), so corridor endpoints
    come from the page's own description rather than from title parsing.
  - The vote-type vocabulary matches Sacramento's project mix: bikeways,
    complete streets, trails/parkways, river bridges, transit, school safety.

    # 1. Geocode + classify every project into a castable plan
    python3 import_to_map.py plan --projects sac_projects.jsonl -o plan.jsonl \
        --base http://localhost:5001 --map sac-proposals

    # 2. Cast it (idempotent: the voter id is derived from the project URL and
    #    /api/vote is clear-then-cast per voter+type)
    python3 import_to_map.py cast --plan plan.jsonl \
        --base http://localhost:5001 --map sac-proposals

    # (helper) print the custom_vote_types list for POST /api/maps
    python3 import_to_map.py vote-types

Stdlib only.
"""
import argparse
import hashlib
import json
import math
import re
import time
import urllib.parse
import urllib.request

REQUEST_DELAY_S = 0.5   # geocoding goes through Flask to Photon — stay polite
CAST_DELAY_S = 0.15

# Corridor sanity guards (see the NYC importer for the incidents behind each).
# Sacramento sprawls: the Stockton Boulevard corridor project alone runs ~8km
# end to end, so the NYC importer's 8km ceiling cut real projects. The looser
# ceiling is safe because a corridor endpoint here is additionally verified to
# lie on the named street (see geocode(expect=...)), which is what the distance
# guard was standing in for.
MAX_CORRIDOR_KM = 10.0
MAX_CORRIDOR_EDGES = 600
# A routed path much longer than the endpoints' crow distance means an endpoint
# snapped onto a stranded stub (levee trails and river parkways are full of
# them here) and the "corridor" is really a hairpin. Demote those to a pin.
DETOUR_RATIO_MAX = 1.8

# Matches server/cities.py "sacramento" — geocode hits outside are failures.
SAC_BBOX = (-121.580, 38.430, -121.355, 38.690)
GEOCODE_SUFFIX = "Sacramento, CA"

# The Public Works "Transportation Projects" index is curated, but carries a few
# entries that aren't street changes at all.
SKIP_TITLE = re.compile(r"broadband|internet|fiber|last ?mile", re.I)

# The full vote-type vocabulary for the map. Every label the classifier can
# emit MUST appear here, so the map's own list resolves its icon and kind
# (themes.iconForLabel / pointTypeForLabel check the map's vote types first).
VOTE_TYPES: list[dict] = [
    {"label": "Add protected bike lane", "icon": "safety", "pointType": "route"},
    {"label": "Add traffic calming", "icon": "traffic-reduction", "pointType": "route"},
    {"label": "Add bus lane", "icon": "transit", "pointType": "route"},
    {"label": "Add greenway", "icon": "parks", "pointType": "route"},
    {"label": "Widen sidewalk", "icon": "walkways", "pointType": "route"},
    {"label": "Add crosswalk", "icon": "pedestrian-streets", "pointType": "route"},
    {"label": "Repave street", "icon": "public-space", "pointType": "route"},
    {"label": "Lower speed limit", "icon": "traffic-reduction", "pointType": "route"},
    {"label": "Fix dangerous intersection", "icon": "safety", "pointType": "point"},
    {"label": "Street redesign", "icon": "traffic-reduction", "pointType": "point"},
    {"label": "Improve bridge crossing", "icon": "walkways", "pointType": "point"},
    {"label": "Improve bus stop", "icon": "transit", "pointType": "point"},
    {"label": "Improve school crossing", "icon": "accessibility", "pointType": "point"},
    {"label": "Add pedestrian plaza", "icon": "public-space", "pointType": "point"},
]

# First matching rule wins. `kind` is the vote type's point_type: 'route' labels
# surface as corridor (RBTP) proposals and MUST be cast on a corridor's edges;
# 'point' labels surface as pins (PBTP).
#
# `scope` decides which text a rule may read. Rules that identify a project by
# a *noun* ("school", "bus stop", "bridge", "intersection") are title-only:
# every Vision Zero page's prose mentions schools, bus stops and intersections
# in passing, and letting those match the body sent half the corridor projects
# to the wrong pin. Rules that name a *treatment* ("bike lane", "trail",
# "repave") are safe to read from the body, which is often where the actual
# scope of work is spelled out.
SCOPE_TITLE = "title"   # title + meta description only
SCOPE_ANY = "any"       # ...and the page body as a fallback

CLASSIFY_RULES: list[tuple[str, str, str, str]] = [
    (r"school safety|school crossing|safe routes to school",
     "Improve school crossing", "point", SCOPE_TITLE),
    # Before the bridge rule: an interchange page's own description may talk
    # about the bridge it carries (and the city's CMS has at least one page
    # whose meta description belongs to a different, bridge, project).
    (r"interchange", "Fix dangerous intersection", "point", SCOPE_TITLE),
    (r"bridge replacement|new bridge|bridge project",
     "Improve bridge crossing", "point", SCOPE_TITLE),
    (r"bus stop|stop consolidation", "Improve bus stop", "point", SCOPE_TITLE),
    (r"transit enhancement|bus lane|bus rapid|transit priority",
     "Add bus lane", "route", SCOPE_ANY),
    # "Parkway" is a plain street-name suffix here (A Parkway, Ninos Parkway),
    # so the trail rule keys off trail/greenway words, not the suffix alone.
    (r"\btrails?\b|greenway|multi-?use path|shared-?use path|bike path|"
     r"river parkway|creek revitaliz", "Add greenway", "route", SCOPE_ANY),
    (r"bikeway|bike lane|class iv|protected bike|separated bike|bicycl|\bbike\b",
     "Add protected bike lane", "route", SCOPE_ANY),
    (r"gateway|plaza|streetscape|public art", "Add pedestrian plaza", "point", SCOPE_TITLE),
    (r"\bintersection\b", "Fix dangerous intersection", "point", SCOPE_TITLE),
    (r"sidewalk", "Widen sidewalk", "route", SCOPE_ANY),
    (r"crosswalk", "Add crosswalk", "route", SCOPE_ANY),
    (r"rehabilitation|repave|resurfac|slurry seal|pavement",
     "Repave street", "route", SCOPE_ANY),
    (r"speed limit", "Lower speed limit", "route", SCOPE_TITLE),
    # A corridor project that calls itself Vision Zero / a complete street /
    # a safety project is a road diet first and a bike project incidentally.
    # This sits AFTER the treatment rules but is title-scoped, so it beats the
    # body-scoped bike rule: without it every complete-street page in the city
    # collapsed onto "Add protected bike lane", because they all mention bike
    # lanes somewhere in the prose.
    (r"vision zero|complete street|road diet|safety (project|improvements?)|"
     r"corridor improvement", "Add traffic calming", "route", SCOPE_TITLE),
    (r"", "Add traffic calming", "route", SCOPE_ANY),
]

# A route-kind classification whose corridor endpoints could NOT be parsed still
# needs to be visible: point casts of route-kind types are excluded from pin
# selection by design. Substitute a point-kind catch-all so the project gets a
# pin at its geocoded location instead of silently painting nothing.
POINT_FALLBACK_LABEL = "Street redesign"

STREET_WORD = (r"Street|St\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Road|Rd\.?|Drive|Dr\.?|"
               r"Way|Lane|Ln\.?|Parkway|Pkwy\.?|Court|Circle|Trail|Broadway|Expressway|"
               r"Freeway|Alley|Place|Pl\.?")
# Suffixes that never stand alone as a street name. Deliberately excludes
# Broadway / Trail / Expressway / Freeway, which are complete names here —
# rejecting a bare "Broadway" silently dropped the corridor for both Broadway
# Complete Streets and Broadway Vision Zero.
SUFFIX_ONLY = (r"Street|St\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|Road|Rd\.?|Drive|Dr\.?|"
               r"Way|Lane|Ln\.?|Parkway|Pkwy\.?|Court|Circle|Alley|Place|Pl\.?")
STREET = r"[A-Z0-9][\w.'&-]*(?:\s+[A-Z0-9][\w.'&-]*){0,4}"

# End of a corridor's second endpoint. A bare `\.` cannot be the terminator:
# "Martin Luther King Jr. Boulevard" would then end at "Jr", losing the street
# entirely. So a period only terminates when it is NOT an abbreviation sitting
# in front of a street-type word.
END = rf"(?:[,;)]|\.(?!\s*(?:{STREET_WORD}))|\s+in\b|\s*$)"
ENDPOINT = r"[A-Z0-9][\w .'&/-]+?"

# "on|along <STREET> [in <Neighborhood>][,] between|from <A> and|to <B>"
CORRIDOR_ON_RE = re.compile(
    rf"(?:on|along)\s+(?:the\s+)?({STREET})"
    rf"(?:\s+in\s+[A-Z][\w' -]{{2,30}}?)?\s*,?\s+"
    rf"(?:between|from)\s+({ENDPOINT})\s+(?:and|to)\s+({ENDPOINT}){END}")

# "<STREET> corridor between|from <A> and|to <B>" — the "two-mile Broadway
# corridor between 3rd Street and 29th Street" shape.
CORRIDOR_NAMED_RE = re.compile(
    rf"({STREET})\s+corridor\s+(?:between|from)\s+({ENDPOINT})"
    rf"\s+(?:and|to)\s+({ENDPOINT}){END}")

# Bare limits ("...improvements from 24th St to Munson Way") — the street then
# has to come from the project title.
LIMITS_RE = re.compile(
    rf"(?:between|from)\s+({ENDPOINT})\s+(?:and|to)\s+({ENDPOINT}){END}")

INTERSECTION_RE = re.compile(
    rf"([A-Z][\w .'-]*?(?:{STREET_WORD}))\s+(?:and|at|&)\s+"
    rf"([A-Z][\w .'-]*?(?:{STREET_WORD}))\b")

# Leading street name in a project title ("Folsom Boulevard Safety Improvements
# Project" → "Folsom Boulevard"; "Florin Road Vision Zero Rehabilitation" →
# "Florin Road").
TITLE_STREET_RE = re.compile(rf"^(?:The\s+)?([A-Z0-9][\w.'&-]*(?:\s+[A-Z0-9][\w.'&-]*)*?\s+(?:{STREET_WORD}))\b")


def http_json(url: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "city-edit-import/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def in_sacramento(lat: float, lon: float) -> bool:
    w, s, e, n = SAC_BBOX
    return s <= lat <= n and w <= lon <= e


def straight_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    dy = (a[0] - b[0]) * 111.32
    dx = (a[1] - b[1]) * 111.32 * math.cos(lat)
    return (dx * dx + dy * dy) ** 0.5


def street_keys(street: str) -> list[str]:
    """Distinctive tokens of a street name, for verifying a geocode hit.

    Suffixes are dropped ("Boulevard"), as are tokens too short to
    discriminate — "T Street" and "9th Street" carry no usable key, so those
    endpoints simply go unverified rather than being matched on a stray "T".
    """
    keys = []
    for tok in re.split(r"[\s.]+", street):
        tok = tok.strip(" .,'&-")
        if len(tok) >= 4 and not re.fullmatch(STREET_WORD, tok, re.I):
            keys.append(tok.lower())
    return keys


def geocode(base: str, slug: str, query: str,
            expect: str | None = None) -> tuple[float, float] | None:
    """First in-bounds hit for `query`, optionally verified against `expect`.

    Photon answers an unrecognised intersection with its best single-street
    guess anywhere in the bbox: "Broadway & Franklin Boulevard" came back as
    Franklin at Cosumnes River Boulevard, 9km south of the real Oak Park
    corner. Nothing downstream can tell that apart from a good hit — the
    coordinates are inside the city and the distance guard only sees a long
    corridor — so when we know which street the point must lie on, the
    result's own display_name has to name it.
    """
    time.sleep(REQUEST_DELAY_S)
    url = f"{base}/api/geocode?" + urllib.parse.urlencode({"q": query, "map": slug})
    try:
        results = http_json(url).get("results") or []
    except Exception:
        return None
    keys = street_keys(expect) if expect else []
    for r in results:
        lat, lon = float(r["lat"]), float(r["lon"])
        if not in_sacramento(lat, lon):
            continue
        if keys:
            shown = (r.get("display_name") or "").lower()
            if not any(k in shown for k in keys):
                continue
        return lat, lon
    return None


def classify(title: str, description: str, body: str) -> tuple[str, str]:
    """Title+description first; the body only breaks a catch-all tie.

    Project pages narrate everything the project touches, so a single pass over
    the whole page would route almost every Vision Zero project to whichever
    keyword happened to appear first. Hence two passes with different rule
    scopes — see CLASSIFY_RULES.
    """
    catch_all = CLASSIFY_RULES[-1]
    head = f"{title}. {description}"
    for text, allowed in ((head, {SCOPE_TITLE, SCOPE_ANY}), (body, {SCOPE_ANY})):
        for pattern, label, kind, scope in CLASSIFY_RULES:
            if pattern and scope in allowed and re.search(pattern, text, re.I):
                return label, kind
    return catch_all[1], catch_all[2]


# An endpoint capture ends at its street-type word; anything after it is the
# sentence running on ("6th Street by providing a single westbound lane").
ENDPOINT_TRIM_RE = re.compile(rf"^(.*?\b(?:{STREET_WORD}))\b", re.I)


def clean_endpoint(raw: str) -> str:
    """Trim a parsed corridor endpoint to something geocodable.

    Sacramento writes alternates with a slash ("Citrusparke Avenue/Parkchannel
    Way") — keep the first. NOTE the leading article is only stripped for
    "the": "A Parkway" is a real street in south Sacramento, and stripping a
    bare "A" turned that endpoint into the meaningless "Parkway".
    """
    part = raw.split("/")[0].strip(" .,;")
    part = re.sub(r"^the\s+", "", part, flags=re.I)
    m = ENDPOINT_TRIM_RE.match(part)
    if m:
        part = m.group(1)
    else:
        part = " ".join(part.split()[:4])
    return part.strip(" .,;")


def street_name(raw: str) -> str:
    """Trim a corridor match's street capture to the trailing street name.

    "This project is located on Broadway" matches CORRIDOR_ON_RE's lazy prefix
    generously; keep only the trailing run of capitalized/numbered tokens.
    """
    words = raw.strip(" .,").split()
    run: list[str] = []
    for w in reversed(words):
        if re.match(r"^[A-Z0-9]", w) or w.lower() in ("of", "the", "and"):
            run.append(w)
        else:
            break
    run.reverse()
    # Those lowercase connectors are allowed *inside* the run ("Martin Luther
    # King Jr. Boulevard" needs none, but "N Street and I Street" does) — never
    # at the front, where they are the sentence, not the name ("of L Street").
    while run and run[0].lower() in ("of", "the", "and"):
        run.pop(0)
    return " ".join(run) if run else raw.strip(" .,")


def street_variants(raw: str):
    """The captured street, then progressively shorter tails of it.

    street_name() keeps the whole trailing run of capitalized tokens, which
    over-reaches when prose runs a proper noun straight into the street
    ("...the Envision Broadway project ... on Broadway between..." captures
    "Envision Broadway"). Rather than guess where the name starts, hand the
    geocoder each tail and let the first one that resolves to two in-bounds
    endpoints win.
    """
    words = raw.split()
    seen = set()
    for start in range(len(words)):
        cand = " ".join(words[start:])
        if cand and cand not in seen:
            seen.add(cand)
            yield cand


def corridor_candidates(title: str, description: str, body: str):
    """Yield (street, a, b) corridor guesses, best-evidence first."""
    for text in (description, body):
        if not text:
            continue
        for rx in (CORRIDOR_ON_RE, CORRIDOR_NAMED_RE):
            m = rx.search(text)
            if m:
                a, b = clean_endpoint(m.group(2)), clean_endpoint(m.group(3))
                for street in street_variants(street_name(m.group(1))):
                    yield street, a, b
    # Limits stated without repeating the street: take it from the title.
    tm = TITLE_STREET_RE.match(title)
    if tm:
        for text in (description, body):
            m = LIMITS_RE.search(text or "")
            if m:
                yield (tm.group(1),
                       clean_endpoint(m.group(1)), clean_endpoint(m.group(2)))


def plan_project(base: str, slug: str, proj: dict) -> dict | None:
    title = (proj.get("title") or "").strip()
    if proj.get("error") or not title or SKIP_TITLE.search(title):
        return None
    description = (proj.get("description") or "").strip()
    body = (proj.get("body") or "").strip()
    label, kind = classify(title, description, body)

    entry = {"url": proj["url"], "title": title, "description": description,
             "vote_type": label, "kind": kind}

    corridor_street = None
    if kind == "route":
        seen: set[tuple[str, str, str]] = set()
        for street, a, b in corridor_candidates(title, description, body):
            if not street or not a or not b or a.lower() == b.lower():
                continue
            # A capture trimmed down to a bare suffix ("Boulevard") is not a
            # street — geocoding it would silently anchor the corridor to
            # whatever Photon guesses first.
            if re.fullmatch(SUFFIX_ONLY, street, re.I):
                continue
            if (street, a, b) in seen:
                continue
            seen.add((street, a, b))
            corridor_street = corridor_street or street
            start = geocode(base, slug, f"{street} & {a}, {GEOCODE_SUFFIX}", expect=street)
            end = geocode(base, slug, f"{street} & {b}, {GEOCODE_SUFFIX}", expect=street)
            if (start and end and start != end
                    and straight_km(start, end) <= MAX_CORRIDOR_KM):
                entry.update(cast="route", start=start, end=end,
                             query=f"{street}: {a} → {b}")
                return entry

    for q in point_queries(title, description, body, corridor_street):
        pt = geocode(base, slug, q)
        if pt:
            if kind == "route":
                # Corridor project without usable endpoints — pin it with the
                # point-kind catch-all so it still shows on the map.
                entry.update(vote_type=POINT_FALLBACK_LABEL, kind="point")
            entry.update(cast="point", point=pt, query=q)
            return entry

    entry.update(cast="skip", reason="geocode-failed")
    return entry


def is_placeable(name: str) -> bool:
    """Reject captures that would geocode to somewhere arbitrary.

    A bare suffix ("Boulevard"), a fragment that *starts* with one ("Street
    Expressway", pulled out of mid-sentence prose), or a single generic noun
    ("School", all that survives stripping "Vision Zero School Safety Project -
    Phase 2") all resolve to *something* inside the city bbox — which is worse
    than no pin at all, because the map would assert a wrong location.
    """
    name = name.strip()
    if not name or re.fullmatch(SUFFIX_ONLY, name, re.I):
        return False
    if re.match(rf"^(?:{SUFFIX_ONLY})\b", name, re.I):
        return False
    return len(name.split()) > 1 or bool(re.search(rf"\b(?:{STREET_WORD})\b", name, re.I))


def point_queries(title: str, description: str, body: str,
                  prefer_street: str | None = None):
    # A corridor project whose endpoints wouldn't verify still knows its own
    # street, and that is the only trustworthy pin for it. It has to come
    # first, because the prose that names the corridor's *limits* ("on
    # Marysville Boulevard between Arcade Boulevard and North Avenue") reads to
    # INTERSECTION_RE as a crossing of the two limit streets — a corner that
    # is nowhere near the project.
    if prefer_street and is_placeable(prefer_street):
        yield f"{prefer_street}, {GEOCODE_SUFFIX}"
    m = INTERSECTION_RE.search(f"{title}. {description}") or INTERSECTION_RE.search(body)
    if m:
        # The capture starts at the first capital in the sentence, so it can
        # drag in prose ("Bikeways will be on the right side of the street on
        # 10th Street"); keep only the trailing street name.
        a, b = street_name(m.group(1)), street_name(m.group(2))
        # "Auburn Boulevard at Auburn Blvd" is prose repeating one street, not
        # a crossing — geocoding it just picks a point somewhere along it.
        same = street_keys(a) and street_keys(a) == street_keys(b)
        if is_placeable(a) and is_placeable(b) and not same:
            yield f"{a} & {b}, {GEOCODE_SUFFIX}"
    tm = TITLE_STREET_RE.match(title)
    if tm and is_placeable(tm.group(1)):
        yield f"{tm.group(1)}, {GEOCODE_SUFFIX}"
    clean = re.sub(r"\b(Project|Phase \w+|Program|Study|Plan|Improvements?|"
                   r"Safety|Rehabilitation|Replacement|Gap Closure|Vision Zero)\b",
                   "", title, flags=re.I)
    clean = re.sub(r"\s{2,}", " ", clean).strip(" -:,")
    if is_placeable(clean):
        yield f"{clean}, {GEOCODE_SUFFIX}"


def voter_id(url: str) -> str:
    return "sacproj:" + hashlib.sha1(url.encode()).hexdigest()[:16]


def cast_entry(base: str, slug: str, entry: dict, mode: str = "walk") -> dict:
    if entry.get("cast") == "route":
        route = http_json(f"{base}/api/routes", {
            "map": slug, "start": list(entry["start"]), "end": list(entry["end"]),
        })
        edge_ids = route.get("edge_ids") or []
        if not edge_ids:
            return {"status": "route-failed"}
        if len(edge_ids) > MAX_CORRIDOR_EDGES:
            return {"status": "route-too-long", "edges": len(edge_ids)}
        crow_km = straight_km(entry["start"], entry["end"])
        routed_km = ((route.get("route") or {}).get("distance") or 0) / 1000.0
        if routed_km > max(0.4, DETOUR_RATIO_MAX * crow_km):
            entry.update(vote_type=POINT_FALLBACK_LABEL, kind="point",
                         cast="point", point=list(entry["start"]),
                         demoted=f"route-detour {routed_km:.1f}km/{crow_km:.1f}km")
            payload = {"point": list(entry["point"])}
        else:
            payload = {"edge_ids": edge_ids}
    elif entry.get("cast") == "point":
        payload = {"point": list(entry["point"])}
    else:
        return {"status": "skipped"}

    time.sleep(CAST_DELAY_S)
    resp = http_json(f"{base}/api/vote", {
        "map": slug, "mode": mode, "vote_type": entry["vote_type"],
        "point_type": entry["kind"], "voter_id": voter_id(entry["url"]),
        "direction": 1, "ip_from_voter": True, **payload,
    })
    if resp.get("error"):
        return {"status": "error", "detail": resp["error"]}
    return {"status": "ok-detour-pin" if entry.get("demoted") else "ok",
            "edges": len(payload.get("edge_ids", [])) or 1}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("phase", choices=["plan", "cast", "vote-types"])
    ap.add_argument("--projects", help="plan: input JSONL from fetch_projects.py")
    ap.add_argument("--plan", help="cast: plan JSONL produced by the plan phase")
    ap.add_argument("-o", "--out", help="plan: output path (default plan.jsonl)")
    ap.add_argument("--base", default="http://localhost:5001")
    ap.add_argument("--map", dest="slug", default="sac-proposals")
    ap.add_argument("--limit", type=int, default=0, help="stop after N entries")
    args = ap.parse_args()

    if args.phase == "vote-types":
        print(json.dumps(VOTE_TYPES, indent=2))
        return

    if args.phase == "plan":
        out_path = args.out or "plan.jsonl"
        seen_titles: set[str] = set()
        stats: dict[str, int] = {}
        with open(args.projects) as f, open(out_path, "w") as out:
            for i, line in enumerate(f):
                if args.limit and i >= args.limit:
                    break
                proj = json.loads(line)
                title = (proj.get("title") or "").strip().lower()
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                entry = plan_project(args.base, args.slug, proj)
                if not entry:
                    stats["filtered"] = stats.get("filtered", 0) + 1
                    continue
                stats[entry["cast"]] = stats.get(entry["cast"], 0) + 1
                out.write(json.dumps(entry) + "\n")
                out.flush()
                print(f"[{i}] {entry['cast']:>5}  {entry['title'][:52]:<52}"
                      f" → {entry['vote_type']}"
                      f"{'  | ' + entry['query'] if entry.get('query') else ''}",
                      flush=True)
        print(f"PLAN DONE {stats} -> {out_path}", flush=True)
    else:
        stats = {}
        with open(args.plan) as f:
            entries = [json.loads(line) for line in f]
        if args.limit:
            entries = entries[:args.limit]
        for i, entry in enumerate(entries):
            result = cast_entry(args.base, args.slug, entry)
            stats[result["status"]] = stats.get(result["status"], 0) + 1
            print(f"[{i}] {result['status']:>12}  {entry['title'][:60]}", flush=True)
        print(f"CAST DONE {stats}", flush=True)


if __name__ == "__main__":
    main()
