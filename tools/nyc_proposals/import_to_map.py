#!/usr/bin/env python3
"""
Import official NYC DOT street-change proposals as votes on a City Edit map.

Input: a JSONL of nycdotprojects.info project pages (url, title, description —
see fetch_latest.py / docs/nyc-proposal-data-sources.md). Two phases so the
plan can be reviewed before anything is cast:

    # 1. Geocode + classify every project into a castable plan
    python3 import_to_map.py plan --projects dot_projects.jsonl -o plan.jsonl \
        --base http://localhost:5001 --map nyc-proposals

    # 2. Cast the plan's votes (idempotent: voter_id is derived from the
    #    project URL, and /api/vote is clear-then-cast per voter+type)
    python3 import_to_map.py cast --plan plan.jsonl \
        --base http://localhost:5001 --map nyc-proposals

Each project becomes ONE vote from its own synthetic voter
(`dotproj:<sha1(url)>`, sent with ip_from_voter so the per-IP cap never
collapses the batch — same convention as the Lyft/Citibike imports):

- Corridor projects ("X from A to B") vote on the whole corridor: geocode both
  endpoints, `POST /api/routes` for the edge ids, cast a route vote. This is
  what lets route-kind vote types surface as corridor (RBTP) proposals.
- Point projects (intersections, squares) cast on the snapped edge under the
  geocoded point.

Everything goes through the public HTTP API — the server does all snapping, so
there is no graph-version coupling and the same plan can be cast against
local, staging, and prod. Stdlib only.
"""
import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request

REQUEST_DELAY_S = 0.5   # geocoding goes through Flask to Photon — stay polite
CAST_DELAY_S = 0.15

# Corridor sanity: DOT corridor projects run along ONE street, so endpoints
# beyond this straight-line distance mean a geocode grabbed the wrong borough's
# same-named street (e.g. "5th Ave & 57 St" hitting Sunset Park instead of
# Midtown). Such entries demote to a point cast at the project's best hit.
MAX_CORRIDOR_KM = 8.0
# And the routed corridor may not resolve to absurdly many edges (a cross-
# borough route is ~700+; the longest legitimate corridors are ~400).
MAX_CORRIDOR_EDGES = 600
# A shortest path much longer than the endpoints' straight-line distance means
# an endpoint snapped onto a stranded path whose only exit is far away — e.g.
# the ESCR-era East River esplanade, one-exit-at-the-south, which turned the
# "E River corridor" cast into a 7.75km hairpin doubling along two parallel
# waterfront stretches (2.6× crow). DOT corridors run along ONE street, so
# their ratio stays near 1; beyond this the route is not the project's street.
DETOUR_RATIO_MAX = 1.8

# NYC bounds — geocode hits outside are treated as failures.
NYC_BBOX = (-74.26, 40.49, -73.70, 40.92)

BOROUGHS = ["Brooklyn", "Bronx", "Queens", "Manhattan", "Staten Island"]

# Pages on nycdotprojects.info that are not street-change proposals. The bare
# slugs mix real project homepages with outreach/blog posts ("Thanks for
# attending!", "DOT at the Queens Pride Festival"), so filtering is two-sided:
# a junk blacklist AND a required project-shaped token in the title.
SKIP_TITLE = re.compile(
    r"feedback map|newsletter|survey|^home$|^about|street ambassador|"
    r"art program|^press |publication|"
    r"thanks?\b|thank you|join us|workshop|meeting|open house|save the date|"
    r"welcome|presentation|come |miss our|recap|ribbon|tabling|giveaway|"
    r"parade|festival|tour|clean-?up|volunteer|celebration|update|released|"
    r"introduction|announce|construction|stay tuned|kick-?off|video|vimeo|"
    r"portal|comment|your (input|thoughts|feedback|participation)|"
    r"\btest\b|sample|archive|places to visit|what's|what were|riders|"
    r"conversations|visits?\b|dot at |suggest|scooter|carshare|charger|"
    r"ferry|rezoning|loading zone|pay-by-app|implemented|"
    r"greenway (grub|gatherings|groundwork|change)|waterfront fun|"
    r"ride the|beautify|graphics on|bonanza|one day plaza|outreach|"
    r"presented|\bmap\b|charging|^greenways$|bike east|route selection",
    re.I)

# ...and the title must look like a street/place/project, not a sentence.
PROJECT_TITLE = re.compile(
    r"street|\bst\b|avenue|\bave\b|boulevard|blvd|road|\brd\b|broadway|"
    r"concourse|parkway|pkwy|square|\bsq\b|plaza|bridge|corridor|busway|"
    r"greenway|bikeway|bike network|bike boulevard|\bsbs\b|study|plan\b|"
    r"redesign|re-?imagin|improvements?|safety|area\b|neighborhood|downtown|"
    r"waterfront|crosstown|connector|intersection|slip lane|shared st|"
    r"\bbikes?\b|\bcurbs?\b|\bbus(es)?\b",
    re.I)


def looks_like_project(title: str) -> bool:
    return (bool(title) and not SKIP_TITLE.search(title)
            and bool(PROJECT_TITLE.search(title)))


# DOT's states for work that is still a proposal. Deliberately an allowlist:
# this map is about what a street *could* become, so a project that has reached
# Installation, Complete or Past Project has stopped being a proposal and
# belongs to the built city instead. A denylist would import every state DOT
# invents next, and the failure direction matters — importing a finished
# project puts a change on the map that is already on the ground.
ACTIVE_STATES = {"planning", "data collection & feedback"}


def is_active_project(proj: dict) -> bool:
    """Whether DOT still calls this project a proposal.

    Unknown states (an unparseable page, a name DOT hasn't used before) count
    as inactive. The 2026-08-12 audit of the first 60 imports is the evidence:
    all 25 it kept were Planning or Data Collection & Feedback, and all 35 it
    removed were something else — including every one of the five whose state
    wouldn't parse. Skipping what we can't read reproduces that call exactly.
    """
    return (proj.get("state") or "").strip().lower() in ACTIVE_STATES

# First matching rule wins. kind is the vote type's point_type: 'route' labels
# surface as corridor (RBTP) proposals and MUST be cast on a corridor's edges;
# 'point' labels surface as pins (PBTP) and are cast on the snapped point.
CLASSIFY_RULES: list[tuple[str, str, str]] = [
    (r"busway|select bus|\bsbs\b|bus lane|bus priority", "Add bus lane", "route"),
    (r"bike|bicycle|greenway|cycl", "Add protected bike lane", "route"),
    (r"plaza|open street|shared street", "Add pedestrian plaza", "point"),
    (r"speed limit", "Lower speed limit", "route"),
    (r"sidewalk", "Widen sidewalk", "route"),
    (r"raised cross", "Add raised crosswalk", "point"),
    (r"crosswalk", "Add crosswalk", "route"),
    (r"daylight", "Daylight this corner", "point"),
    (r"curb extension|neckdown", "Add curb extension", "point"),
    (r"refuge|median|pedestrian island", "Add pedestrian refuge island", "point"),
    (r"signal timing|retim", "Fix signal timing", "point"),
    (r"signal|leading pedestrian", "Add pedestrian signal", "point"),
    (r"lighting", "Add intersection lighting", "point"),
    (r"turn on red", "Ban turn on red", "point"),
    (r"intersection|\bsquare\b|triangle|\bcircle\b", "Fix dangerous intersection", "point"),
    # DOT corridor redesigns / safety projects with no sharper signal.
    (r"", "Add traffic calming", "route"),
]

# A route-kind classification whose corridor endpoints could NOT be parsed
# still needs to be visible: point casts of route-kind types are excluded from
# pin selection by design (their family is corridors). Substitute a point-kind
# catch-all so the project gets a pin at its geocoded location instead of
# silently painting nothing.
POINT_FALLBACK_LABEL = "Street redesign"

# "X from A to B" / "X between A and B" / "X, A to B" — corridor with endpoints.
CORRIDOR_RE = re.compile(
    r"([A-Z][\w .'&-]+?)\s+(?:from|between)\s+([A-Z0-9][\w .'&-]+?)"
    r"\s+(?:to|and)\s+([A-Z0-9][\w .'&-]+?)(?:[,.;)|]|$)")
TITLE_SPAN_RE = re.compile(r"^([^,]+),\s*(.+?)\s+to\s+(.+)$")


def street_name(raw: str) -> str:
    """Trim a corridor match's street capture to the actual street name.

    Prose like "Multi-phase safety improvements on Linden Boulevard" matches
    CORRIDOR_RE's lazy prefix; keep only the trailing run of capitalized/
    numbered tokens ("Linden Boulevard", "E 161st Street").
    """
    words = raw.strip(" .,").split()
    run: list[str] = []
    for w in reversed(words):
        if re.match(r"^[A-Z0-9]", w) or w in ("of", "the"):
            run.append(w)
        else:
            break
    return " ".join(reversed(run)) if run else raw.strip(" .,")
INTERSECTION_RE = re.compile(
    r"([A-Z][\w .'-]+?(?:Street|Avenue|Boulevard|Road|Place|Parkway|Broadway|Concourse))"
    r"\s+(?:and|at|&)\s+([A-Z][\w .'-]+?(?:Street|Avenue|Boulevard|Road|Place|Parkway|Broadway|Concourse))")


def http_json(url: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "city-edit-import/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def in_nyc(lat: float, lon: float) -> bool:
    w, s, e, n = NYC_BBOX
    return s <= lat <= n and w <= lon <= e


def straight_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math
    lat = math.radians((a[0] + b[0]) / 2)
    dy = (a[0] - b[0]) * 111.32
    dx = (a[1] - b[1]) * 111.32 * math.cos(lat)
    return (dx * dx + dy * dy) ** 0.5


def geocode(base: str, slug: str, query: str) -> tuple[float, float] | None:
    time.sleep(REQUEST_DELAY_S)
    url = f"{base}/api/geocode?" + urllib.parse.urlencode({"q": query, "map": slug})
    try:
        results = http_json(url).get("results") or []
    except Exception:
        return None
    for r in results:
        lat, lon = float(r["lat"]), float(r["lon"])
        if in_nyc(lat, lon):
            return lat, lon
    return None


def classify(text: str) -> tuple[str, str]:
    for pattern, label, kind in CLASSIFY_RULES:
        if re.search(pattern, text, re.I):
            return label, kind
    return CLASSIFY_RULES[-1][1], CLASSIFY_RULES[-1][2]


def borough_of(text: str) -> str:
    for b in BOROUGHS:
        if re.search(rf"\b{b}\b", text, re.I):
            return b
    return "New York"


def plan_project(base: str, slug: str, proj: dict) -> dict | None:
    title = (proj.get("title") or "").strip()
    if proj.get("error") or not looks_like_project(title):
        return None
    # Gated here so every caller inherits it, including a bulk re-import. Note
    # a projects file scraped before fetch_latest recorded `state` carries none,
    # and so plans as nothing — re-fetch rather than loosening this.
    if not is_active_project(proj):
        return None
    desc = (proj.get("description") or "").strip()
    text = f"{title}. {desc}"
    label, kind = classify(text)
    boro = borough_of(text)

    entry = {"url": proj["url"], "title": title, "description": desc,
             "vote_type": label, "kind": kind}

    # Corridor: two geocodable endpoints along a named street.
    m = CORRIDOR_RE.search(text) or TITLE_SPAN_RE.match(title)
    if m and kind == "route":
        street = street_name(m.group(1))
        a, b = (s.strip(" .,") for s in m.groups()[1:])
        # "E 57 St"/"W 181st" cross streets imply Manhattan — try it first when
        # no borough was named, so same-named streets elsewhere don't win.
        boros = [boro]
        if boro == "New York" and re.search(r"\b[EW]\.? ?\d", f"{a} {b}"):
            boros.insert(0, "Manhattan")
        for try_boro in boros:
            start = geocode(base, slug, f"{street} & {a}, {try_boro}")
            end = geocode(base, slug, f"{street} & {b}, {try_boro}")
            if (start and end and start != end
                    and straight_km(start, end) <= MAX_CORRIDOR_KM):
                entry.update(cast="route", start=start, end=end,
                             query=f"{street}: {a} → {b} ({try_boro})")
                return entry

    # Point: an intersection, or the best single hit for the project name.
    for q in _point_queries(title, text, boro):
        pt = geocode(base, slug, q)
        if pt:
            if kind == "route":
                # Corridor project without parseable endpoints — pin it with
                # the point-kind catch-all so it still shows on the map.
                entry.update(vote_type=POINT_FALLBACK_LABEL, kind="point")
            entry.update(cast="point", point=pt, query=q)
            return entry
    entry.update(cast="skip", reason="geocode-failed")
    return entry


def _point_queries(title: str, text: str, boro: str):
    m = INTERSECTION_RE.search(text)
    if m:
        yield f"{m.group(1).strip()} & {m.group(2).strip()}, {boro}"
    # The title itself, then its leading street name, city-biased.
    clean = re.sub(r"\b(Feedback|Project|Pilot|Redesign|Improvements?|Safety|"
                   r"Study|Phase \w+)\b", "", title, flags=re.I).strip(" -:,")
    if clean:
        yield f"{clean}, {boro}"
    first = re.split(r",| - |:", title)[0].strip()
    if first and first != clean:
        yield f"{first}, {boro}"


def voter_id(url: str) -> str:
    return "dotproj:" + hashlib.sha1(url.encode()).hexdigest()[:16]


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
            # Hairpin detour (see DETOUR_RATIO_MAX): pin the project instead of
            # painting the detour with corridor votes — same demotion (and
            # point-kind label swap) as corridors whose endpoints never parsed.
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
    ap.add_argument("phase", choices=["plan", "cast"])
    ap.add_argument("--projects", help="plan: input JSONL from fetch_all/fetch_latest")
    ap.add_argument("--plan", help="cast: plan JSONL produced by the plan phase")
    ap.add_argument("-o", "--out", help="plan: output path (default plan.jsonl)")
    ap.add_argument("--base", default="http://localhost:5001")
    ap.add_argument("--map", dest="slug", default="nyc-proposals")
    ap.add_argument("--limit", type=int, default=0, help="stop after N entries")
    args = ap.parse_args()

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
                print(f"[{i}] {entry['cast']:>5}  {entry['title'][:60]}"
                      f"  → {entry['vote_type']}", flush=True)
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
