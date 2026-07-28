#!/usr/bin/env python3
"""
Procedurally generate street-safety posters for NYC's most dangerous
intersections, one per (campaign x intersection), each with a QR deep link
into City Edit at that exact intersection.

Inputs:
  - data/analysis/intersections_master.csv   (from build_intersection_rankings.py)
  - data/posters/templates/<campaign>.html   (replica/novel templates with slot
    ids: headline / statline / cta / qr / neighborhood — leaf elements)

Campaigns (danger definition -> template -> copy):
  ped_no1     rank by recency-decayed pedestrian score -> "THE #<n> MOST
              DANGEROUS INTERSECTION FOR PEDESTRIANS IN NYC" (poster 2 replica)
  cyc_no1     recency-decayed cyclist score (poster 3 replica)
  overall     recency-decayed combined score (poster 1 "is killer!" replica)
  dark        merged ped+cyclist danger (rank-sum of both lists; poster 4 dark)
  nabe_no1    per-NTA-neighborhood #1 by fresh weighted score (novel)
  after_dark  night-crash share among active intersections (novel)

Usage:
  python scripts/generate_posters.py                # all campaigns, default Ns
  python scripts/generate_posters.py --campaign nabe_no1 --limit 40 --scale 2
"""

import argparse
import csv
import html
import re
import subprocess
from pathlib import Path

import qrcode

REPO = Path(__file__).resolve().parent.parent
MASTER = REPO / "data" / "analysis" / "intersections_master.csv"
TEMPLATES = REPO / "data" / "posters" / "templates"
OUT = REPO / "data" / "posters" / "out"
RENDER = REPO / "scripts" / "render_poster.sh"
BASE_URL = "https://cityedit.org/m/nyc-intersections"
# Preselected vote type carried in every QR: scanning lands with the pin set
# and this (point-kind) type ready to cast.
QR_VOTE_TYPE = "Fix dangerous intersection"


def load_rows():
    rows = list(csv.DictReader(open(MASTER)))
    num = ["crashes", "ped_inj", "ped_kill", "cyc_inj", "cyc_kill", "f12_crashes",
           "f12_ped_inj", "f12_ped_kill", "f12_cyc_inj", "f12_cyc_kill",
           "score_f12_all_w", "recency_score", "recency_ped", "recency_cyc",
           "base_rate_12mo", "heat_surprise", "cool_surprise", "night_share"]
    for r in rows:
        for k in num:
            r[k] = float(r[k])
    return rows


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


def fill_slot(doc, slot_id, content):
    """Replace the inner HTML of the (leaf) element carrying id=slot_id.

    Only safe for leaf slots (no nested same-tag children) — structured slots
    like the ped_no1 headline are edited via targeted `swaps` instead."""
    pat = re.compile(r'(<(\w+)[^>]*\bid="' + slot_id + r'"[^>]*>).*?(</\2>)', re.S)
    if not pat.search(doc):
        return doc
    return pat.sub(lambda m: m.group(1) + content + m.group(3), doc)


def set_style(doc, slot_id, extra_css):
    """Append inline CSS to the element carrying id=slot_id."""
    pat = re.compile(r'<(\w+)([^>]*\bid="' + slot_id + r'"[^>]*)>')
    def sub(m):
        attrs = m.group(2)
        if 'style="' in attrs:
            attrs = attrs.replace('style="', f'style="{extra_css};', 1)
        else:
            attrs += f' style="{extra_css}"'
        return f"<{m.group(1)}{attrs}>"
    return pat.sub(sub, doc, count=1)


# Short per-poster codes stamped tiny on each poster and listed in the
# placement checklist, so a stack of printed posters maps back to
# intersections without reading QR codes. Deliberately neutral (A1, B7, …)
# — a passerby can't infer campaign structure from them.
def code_allocator():
    n = 0
    def next_code():
        nonlocal n
        code = f"{chr(65 + n // 9)}{n % 9 + 1}"
        n += 1
        return code
    return next_code


def stamp_footer(doc, code, dark=False, pos="bottom"):
    """Tiny marks on every poster: placement code in one corner, City Edit
    wordmark (square boxed letters, echoing the app's tile logo) + cityedit.org
    in the other. `pos="top"` for designs whose bottom edge is dark artwork
    (e.g. the cyclist poster's black road) where a footer can't be read."""
    ink = "#d9d9d9" if dark else "#4a4a4a"
    dim = "#9b9b9b" if dark else "#8a8a8a"
    box = (
        "display:inline-block;border:1.3px solid " + ink + ";"
        "padding:0 2px;margin:0 0.5px;font:700 8px ui-monospace,Menlo,monospace;"
        "line-height:11px;color:" + ink + ";"
    )
    letters = "".join(
        f'<span style="{box}">{ch}</span>' if ch != " " else
        '<span style="display:inline-block;width:4px;"></span>'
        for ch in "CITY EDIT")
    anchor = "top:6px" if pos == "top" else "bottom:5px"
    tag = (
        f'<div style="position:absolute;left:10px;{anchor};z-index:99;'
        "font:600 11px ui-monospace,Menlo,monospace;letter-spacing:1.5px;"
        f'color:{dim};">{code}</div>'
        f'<div style="position:absolute;right:10px;{anchor};z-index:99;">'
        f'{letters}<span style="font:600 10px ui-monospace,Menlo,monospace;'
        f'color:{dim};margin-left:6px;">cityedit.org</span></div>'
    )
    if "</body>" in doc:
        return doc.replace("</body>", tag + "</body>", 1)
    return doc + tag


# NTA-2020 names are census constructions, not neighborhoods people say out
# loud ("Midtown South-Flatiron-Union Square"). Map the frankensteins to the
# recognizable name for where the target intersection actually sits; anything
# unlisted just loses its parenthetical qualifier. Two NTA halves that map to
# the same display name are MERGED (only the worse intersection keeps the
# "most dangerous in X" claim — see nabe_targets).
NTA_DISPLAY = {
    "Astoria (North)-Ditmars-Steinway": "Ditmars",
    "Midtown South-Flatiron-Union Square": "Midtown South",
    "Midtown-Times Square": "Midtown",
    "Chelsea-Hudson Yards": "Chelsea",
    "East New York-New Lots": "East New York",
    "Pelham Parkway-Van Nest": "Pelham Parkway",
    "Upper East Side-Lenox Hill-Roosevelt Island": "Lenox Hill",
    "East Flatbush-Remsen Village": "East Flatbush",
    "Hamilton Heights-Sugar Hill": "Hamilton Heights",
    "Bedford-Stuyvesant (East)": "Bed-Stuy",
    "Bedford-Stuyvesant (West)": "Bed-Stuy",
    "Gravesend (East)-Homecrest": "Homecrest",
    "Mott Haven-Port Morris": "Mott Haven",
    "University Heights (North)-Fordham": "Fordham",
    "University Heights (South)-Morris Heights": "Morris Heights",
    "Murray Hill-Kips Bay": "Kips Bay",
    "Claremont Village-Claremont (East)": "Claremont",
    "Concourse-Concourse Village": "Concourse",
    "Rockaway Beach-Arverne-Edgemere": "Arverne",
    "Prospect Lefferts Gardens-Wingate": "Prospect Lefferts Gardens",
    "New Dorp-Midland Beach": "New Dorp",
    "Castle Hill-Unionport": "Castle Hill",
    "Murray Hill-Broadway Flushing": "Murray Hill",
    "Old Astoria-Hallets Point": "Old Astoria",
    "Sunnyside Yards (North)": "Sunnyside",
    "Upper West Side-Manhattan Valley": "Manhattan Valley",
    "Wakefield-Woodlawn": "Wakefield",
}


# Neighborhoods New Yorkers say with a definite article: "the Upper West
# Side", never "Upper West Side". The THE joins the big red name so the
# headline reads "…INTERSECTION IN / THE UPPER WEST SIDE".
THE_NEIGHBORHOODS = {
    "Upper West Side", "Upper East Side", "Lower East Side",
    "East Village", "West Village", "Financial District", "Rockaways",
}


def nabe_display(nta):
    """Poster-friendly neighborhood name (see NTA_DISPLAY / THE_NEIGHBORHOODS)."""
    name = NTA_DISPLAY.get(nta) or re.sub(r"\s*\([^)]*\)", "", nta).strip()
    if name in THE_NEIGHBORHOODS:
        name = "The " + name
    return name


def split_two_lines(name):
    """Balance a long name onto two lines at the space minimizing the longer
    line; returns (lines, longest_len)."""
    if len(name) <= 12 or " " not in name:
        return [name], len(name)
    best = None
    for i, ch in enumerate(name):
        if ch == " ":
            a, b = name[:i], name[i + 1:]
            cand = max(len(a), len(b))
            if best is None or cand < best[1]:
                best = ([a, b], cand)
    return best


def night_fraction(share):
    from fractions import Fraction
    f = Fraction(share).limit_denominator(10)
    return f.numerator, f.denominator


def set_qr(doc, qr_relpath):
    return re.sub(r'(<img[^>]*\bid="qr"[^>]*\bsrc=")[^"]*(")',
                  lambda m: m.group(1) + qr_relpath + m.group(2), doc)


def victims_line(r, window="full"):
    if window == "full":
        inj = int(r["ped_inj"] + r["cyc_inj"])
        kil = int(r["ped_kill"] + r["cyc_kill"])
        since = "since 2023"
    else:
        inj = int(r["f12_ped_inj"] + r["f12_cyc_inj"])
        kil = int(r["f12_ped_kill"] + r["f12_cyc_kill"])
        since = "in the last 12 months"
    if inj and kil:
        what = f"{inj} people injured and {kil} killed"
    elif kil:
        what = f"{kil} {'person' if kil == 1 else 'people'} killed"
    else:
        what = f"{inj} {'person' if inj == 1 else 'people'} injured"
    return f"{what} here {since}"


def _nabe_fill(r, rank):
    lines, _ = split_two_lines(nabe_display(r["nta"]).upper())
    return {"neighborhood": "<br>".join(html.escape(ln) for ln in lines),
            "statline": html.escape(victims_line(r))}


def _nabe_style(r, rank):
    # MELROSE (7 chars) renders at the template's native 124px; longer names
    # shrink proportionally, and names past ~12 chars break onto two balanced
    # lines (sized by the longer line, capped so two lines still clear the
    # stat line below).
    lines, longest = split_two_lines(nabe_display(r["nta"]))
    if len(lines) == 2:
        size = max(44, min(74, int(124 * 7.4 / max(longest, 8))))
        return {"neighborhood": f"font-size:{size}px;line-height:1.02"}
    size = max(56, min(124, int(124 * 7.4 / max(longest, 5))))
    return {"neighborhood": f"font-size:{size}px"}


def _after_dark_fill(r, rank):
    num, den = night_fraction(r["night_share"])
    return {"statline": f"<b>{num} out of {den}</b> crashes here happen<br>"
                        f"between <b>9pm and 6am</b>"}


def _dark_select(rows):
    """Merged ped+cyclist danger: sum each intersection's RANK on the
    pedestrian and cyclist recency lists (Borda-style), so the winners are
    dangerous to BOTH modes — a pure victim sum is pedestrian-dominated.
    Requires real signal in each mode."""
    ped_rank = {id(r): i for i, r in enumerate(sorted(rows, key=lambda r: -r["recency_ped"]))}
    cyc_rank = {id(r): i for i, r in enumerate(sorted(rows, key=lambda r: -r["recency_cyc"]))}
    both = [r for r in rows if r["recency_ped"] > 1 and r["recency_cyc"] > 1]
    return sorted(both, key=lambda r: ped_rank[id(r)] + cyc_rank[id(r)])


# Campaigns run in priority order and each intersection is claimed ONCE — a
# location never appears twice in the packet/checklist. `claim` controls how
# a campaign handles an already-claimed intersection:
#   rank    → drop it but keep the true rank number (a "#N" claim must stay true)
#   unique  → drop it (the "#1 in <nabe>" claim can't transfer to a runner-up)
#   generic → substitute the next-best unclaimed one (claim is generic)
CAMPAIGN_ORDER = ["ped_no1", "cyc_no1", "nabe_no1", "dark", "after_dark", "overall"]

CAMPAIGNS = {
    # "#N" rank posters keep their nested per-line headline markup; only the
    # rank number is swapped in place.
    "ped_no1": {
        "template": "ped_no1.html", "default_n": 10, "claim": "rank",
        "select": lambda rows: sorted(rows, key=lambda r: -r["recency_ped"]),
        "swaps": lambda r, rank: [(r"THE #\d+ MOST", f"THE #{rank} MOST")],
        # NOTE: this template's id="statline" is the YOU ARE HERE pin text —
        # the design carries no stat line, so nothing is filled here.
        "fill": lambda r, rank: {},
    },
    "cyc_no1": {
        "template": "cyc_no1.html", "default_n": 10, "claim": "rank",
        "select": lambda rows: sorted(rows, key=lambda r: -r["recency_cyc"]),
        "swaps": lambda r, rank: [(r"THE #\d+ MOST", f"THE #{rank} MOST")],
        "fill": lambda r, rank: {},
        # bottom edge is the black road on both corners — marks go top
        "footer_pos": "top",
    },
    "nabe_no1": {
        "template": "nabe_no1.html", "default_n": 30, "claim": "unique",
        "select": None,  # special-cased: one per neighborhood
        "fill": _nabe_fill,
        "style": _nabe_style,
    },
    "dark": {
        "template": "dark_gear.html", "default_n": 10, "claim": "generic",
        "select": _dark_select,
        "fill": lambda r, rank: {},
        # corner marks sit on baked navy ovals in the night field
        "dark": True,
    },
    "after_dark": {
        "template": "after_dark.html", "default_n": 12, "claim": "generic",
        "select": lambda rows: [r for r in sorted(rows, key=lambda r: -r["night_share"])
                                if r["f12_crashes"] >= 4],
        "fill": _after_dark_fill,
        "dark": True,
    },
    "overall": {
        "template": "bike_killer.html", "default_n": 10, "claim": "generic",
        "select": lambda rows: sorted(rows, key=lambda r: -r["recency_score"]),
        # The mockup's own placeholder is "your <bike thing> is killer!" — fill
        # it with something a bike nerd would grin at that matches the drawn
        # step-through city bike.
        "fill": lambda r, rank: {"headline": "your step-through is killer!"},
    },
}


def nabe_targets(rows, n):
    # Best per DISPLAY neighborhood (not per NTA): NTA halves like East
    # Harlem (North)/(South) merge, so only one poster ever claims "the most
    # dangerous intersection in EAST HARLEM".
    best = {}
    for r in rows:
        if not r["nta"] or r["score_f12_all_w"] < 3:
            continue
        key = nabe_display(r["nta"])
        cur = best.get(key)
        if cur is None or (r["score_f12_all_w"], r["recency_score"]) > (cur["score_f12_all_w"], cur["recency_score"]):
            best[key] = r
    return sorted(best.values(), key=lambda r: -r["score_f12_all_w"])[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", choices=list(CAMPAIGNS), action="append",
                    help="campaign(s) to build (default: all)")
    ap.add_argument("--limit", type=int, help="posters per campaign (default per-campaign)")
    ap.add_argument("--scale", type=int, default=2,
                    help="device scale factor (2 -> 1700x2268 px, good for print)")
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--pdf", action="store_true", default=True,
                    help="assemble out/poster-book.pdf (checklist + all posters)")
    ap.add_argument("--no-pdf", dest="pdf", action="store_false")
    args = ap.parse_args()

    rows = load_rows()
    manifest = []
    next_code = code_allocator()
    used = set()  # (lat, lon) already claimed — one poster per intersection
    for name in [c for c in CAMPAIGN_ORDER if not args.campaign or c in args.campaign]:
        c = CAMPAIGNS[name]
        tpl_path = TEMPLATES / c["template"]
        if not tpl_path.exists():
            print(f"!! {name}: template {tpl_path} missing, skipping")
            continue
        tpl = tpl_path.read_text()
        n = args.limit or c["default_n"]
        cands = nabe_targets(rows, n) if name == "nabe_no1" else c["select"](rows)
        # (row, rank) targets under the campaign's claim policy (see above).
        targets = []
        if c["claim"] == "generic":
            for r in cands:
                if (r["lat"], r["lon"]) not in used:
                    targets.append((r, len(targets) + 1))
                if len(targets) >= n:
                    break
        else:  # rank / unique: never renumber, never substitute
            for rank, r in enumerate(cands[:n], start=1):
                if (r["lat"], r["lon"]) in used:
                    print(f"   {name} #{rank} already claimed, dropped: {r['name_osm']}")
                    continue
                targets.append((r, rank))
        outdir = OUT / name
        outdir.mkdir(parents=True, exist_ok=True)
        for r, rank in targets:
            used.add((r["lat"], r["lon"]))
            from urllib.parse import quote_plus
            url = f'{args.base_url}?w={r["lat"]},{r["lon"]}&vt={quote_plus(QR_VOTE_TYPE)}'
            tag = f"{rank:02d}_{slug(r['name_osm'])}"
            qr_png = outdir / f"{tag}_qr.png"
            qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                               border=1, box_size=12)
            qr.add_data(url)
            qr.make(fit=True)
            # match each template's QR colorway by the asset it references
            dark_qr = "qr_white_on_black" in tpl
            red_qr = "qr_red" in tpl
            qr.make_image(
                fill_color="white" if dark_qr else ("#d0312d" if red_qr else "black"),
                back_color="black" if dark_qr else "white").save(qr_png)

            code = next_code()
            doc = tpl
            for pat, repl in (c["swaps"](r, rank) if "swaps" in c else []):
                doc = re.sub(pat, repl, doc)
            for slot, content in c["fill"](r, rank).items():
                doc = fill_slot(doc, slot, content)
            for slot, css in (c["style"](r, rank).items() if "style" in c else []):
                doc = set_style(doc, slot, css)
            doc = set_qr(doc, qr_png.name)
            doc = stamp_footer(doc, code, dark=c.get("dark", False),
                               pos=c.get("footer_pos", "bottom"))
            html_path = outdir / f"{tag}.html"
            html_path.write_text(doc)
            subprocess.run([str(RENDER), str(html_path), str(outdir / f"{tag}.png"),
                            "850", "1134", str(args.scale)],
                           check=True, capture_output=True)
            manifest.append({"code": code, "campaign": name, "rank": rank,
                             "name": r["name_osm"],
                             "nta": r["nta"], "borough": r["borough"],
                             "lat": r["lat"], "lon": r["lon"], "url": url,
                             "png": str((outdir / f"{tag}.png").relative_to(REPO))})
            print(f"{code}: {r['name_osm']} ({r['nta']})")

    with open(OUT / "manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0].keys()))
        w.writeheader()
        w.writerows(manifest)
    print(f"\n{len(manifest)} posters -> {OUT} (manifest.csv written)")

    if args.pdf:
        build_poster_book(manifest, args.scale)


CAMPAIGN_TITLE = {
    "ped_no1": "Pedestrian #N citywide", "cyc_no1": "Cyclist #N citywide",
    "overall": "Overall (recency)", "dark": "Ped + cyclist merged",
    "nabe_no1": "Neighborhood #1", "after_dark": "After dark",
}

CHECKLIST_ROWS_PER_COL = 20
CHECKLIST_ROWS_PER_PAGE = CHECKLIST_ROWS_PER_COL * 2

CHECKLIST_CSS = """
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; }
  body { margin: 0; width: 850px; height: 1134px; background: #fff;
         font: 14px system-ui, -apple-system, sans-serif; color: #161512;
         padding: 40px 40px; }
  .eyebrow { font-size: 11px; font-weight: 700; letter-spacing: 0.14em;
             text-transform: uppercase; color: #c0674a; }
  h1 { font-size: 26px; font-weight: 800; margin: 4px 0 2px; }
  .sub { color: #6b6760; font-size: 12.5px; margin-bottom: 14px; }
  .cols { display: flex; gap: 26px; }
  .col { flex: 1; min-width: 0; }
  table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
  th { text-align: left; font-size: 10px; text-transform: uppercase;
       letter-spacing: 0.06em; color: #8a857a; padding: 4px 5px;
       border-bottom: 2px solid #161512; }
  td { padding: 5.5px 5px; border-bottom: 1px solid #e8e6de; font-size: 11.5px;
       vertical-align: middle; }
  .box { width: 13px; height: 13px; border: 2px solid #161512;
         display: inline-block; }
  .code { font: 700 11px ui-monospace, Menlo, monospace; letter-spacing: 1px; }
  .nta { color: #6b6760; font-size: 10px; display: block; }
  .pageno { position: absolute; bottom: 16px; right: 40px; color: #9b9b9b;
            font-size: 11px; }
</style>
"""


# Dot colors per campaign for the placement map (distinct on the light map).
CAMPAIGN_COLOR = {
    "ped_no1": "#d0312d", "cyc_no1": "#1c7ed6", "nabe_no1": "#2f9e44",
    "dark": "#111111", "after_dark": "#7048a8", "overall": "#e8890c",
}

NTA_GEOJSON = REPO / "data" / "raw" / "nta2020.geojson"


def build_map_page(manifest, scale):
    """One-page NYC placement map: NTA outlines + campaign-colored dots with
    tiny code labels. Drawn from the local NTA geojson (no tile fetches);
    returns the rendered PNG path, or None if the geojson is missing."""
    import json as _json
    import math
    if not NTA_GEOJSON.exists():
        print(f"!! map page skipped: {NTA_GEOJSON} missing "
              "(NYC Open Data 9nt8-h7nd GeoJSON export)")
        return None
    g = _json.load(open(NTA_GEOJSON))

    W, H, TOP, PAD = 850, 1134, 96, 22
    map_h = H - TOP - 74  # leave room for title above, legend below
    lons, lats = [], []
    def rings(geom):
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            yield poly[0]  # outer ring only — print silhouette
    for f in g["features"]:
        for ring in rings(f["geometry"]):
            for lon, lat in ring:
                lons.append(lon); lats.append(lat)
    lon0, lon1, lat0, lat1 = min(lons), max(lons), min(lats), max(lats)
    coslat = math.cos(math.radians((lat0 + lat1) / 2))
    k = min((W - 2 * PAD) / ((lon1 - lon0) * coslat), map_h / (lat1 - lat0))
    ox = (W - (lon1 - lon0) * coslat * k) / 2
    oy = TOP + (map_h - (lat1 - lat0) * k) / 2
    def xy(lon, lat):
        return (ox + (lon - lon0) * coslat * k, oy + (lat1 - lat) * k)

    paths = []
    for f in g["features"]:
        for ring in rings(f["geometry"]):
            pts = [ring[i] for i in range(0, len(ring), max(1, len(ring) // 120))]
            d = "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in (xy(lo, la) for lo, la in pts)) + "Z"
            paths.append(f"<path d='{d}' fill='#efede6' stroke='#ffffff' stroke-width='0.7'/>")

    dots = []
    for m in manifest:
        x, y = xy(float(m["lon"]), float(m["lat"]))
        c = CAMPAIGN_COLOR[m["campaign"]]
        dots.append(
            f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4' fill='{c}' "
            "stroke='#ffffff' stroke-width='1.2'/>"
            f"<text x='{x + 5.5:.1f}' y='{y + 2.5:.1f}' style=\"font:600 6.5px "
            "ui-monospace,Menlo,monospace;fill:#161512;paint-order:stroke;"
            f"stroke:#ffffff;stroke-width:2px\">{m['code']}</text>")

    legend = "".join(
        f"<span style='display:inline-flex;align-items:center;gap:5px;margin-right:14px'>"
        f"<span style='width:9px;height:9px;border-radius:50%;background:{CAMPAIGN_COLOR[c]};"
        f"display:inline-block'></span>{CAMPAIGN_TITLE[c]}</span>"
        for c in CAMPAIGN_ORDER if c in {m["campaign"] for m in manifest})

    doc = (CHECKLIST_CSS +
           "<div class='eyebrow'>City Edit · Dangerous-intersections campaign</div>"
           "<h1>Poster placement map</h1>"
           "<div class='sub'>One dot per poster; codes match the checklist and "
           "each poster's corner mark.</div>"
           f"<svg width='{W - 80}' height='{H - TOP - 60}' "
           f"viewBox='0 40 {W} {H - TOP - 20}' style='margin:-30px 0 0 -40px'>"
           f"{''.join(paths)}{''.join(dots)}</svg>"
           f"<div style='position:absolute;bottom:14px;left:40px;font-size:11px;"
           f"color:#161512'>{legend}</div>")
    hp = OUT / "map_page.html"
    pp = OUT / "map_page.png"
    hp.write_text(doc)
    subprocess.run([str(RENDER), str(hp), str(pp), "850", "1134", str(scale)],
                   check=True, capture_output=True)
    return pp


def build_poster_book(manifest, scale):
    """Checklist pages + placement map + every poster, one PDF
    (out/poster-book.pdf)."""
    from PIL import Image

    pages = [manifest[i:i + CHECKLIST_ROWS_PER_PAGE]
             for i in range(0, len(manifest), CHECKLIST_ROWS_PER_PAGE)]
    checklist_pngs = []

    def col_table(ms):
        body = "".join(
            f"<tr><td><span class='box'></span></td>"
            f"<td class='code'>{m['code']}</td>"
            f"<td>{html.escape(m['name'])}<span class='nta'>"
            f"{html.escape(m['nta'])} · {html.escape(m['borough'])} · "
            f"{CAMPAIGN_TITLE[m['campaign']]}</span></td></tr>"
            for m in ms)
        return ("<table><tr><th></th><th>Code</th><th>Intersection</th></tr>"
                f"{body}</table>")

    for pi, rows in enumerate(pages):
        left = rows[:CHECKLIST_ROWS_PER_COL]
        right = rows[CHECKLIST_ROWS_PER_COL:]
        cols = f"<div class='col'>{col_table(left)}</div>"
        if right:
            cols += f"<div class='col'>{col_table(right)}</div>"
        doc = (CHECKLIST_CSS +
               "<div class='eyebrow'>City Edit · Dangerous-intersections campaign</div>"
               "<h1>Poster placement checklist</h1>"
               "<div class='sub'>Each poster carries its code in a corner. "
               "Tick when hung; QR links open the intersection on "
               "cityedit.org/m/nyc-intersections.</div>"
               f"<div class='cols'>{cols}</div>"
               f"<div class='pageno'>checklist {pi + 1} / {len(pages)}</div>")
        hp = OUT / f"checklist_{pi + 1}.html"
        pp = OUT / f"checklist_{pi + 1}.png"
        hp.write_text(doc)
        subprocess.run([str(RENDER), str(hp), str(pp), "850", "1134", str(scale)],
                       check=True, capture_output=True)
        checklist_pngs.append(pp)

    map_png = build_map_page(manifest, scale)
    if map_png:
        checklist_pngs.append(map_png)

    sheets = [Image.open(p).convert("RGB") for p in checklist_pngs]
    sheets += [Image.open(REPO / m["png"]).convert("RGB") for m in manifest]
    pdf = OUT / "poster-book.pdf"
    sheets[0].save(pdf, save_all=True, append_images=sheets[1:],
                   resolution=int(72 * scale), quality=85)
    print(f"poster book: {pdf} ({len(sheets)} pages: "
          f"{len(checklist_pngs)} checklist/map + {len(manifest)} posters)")


if __name__ == "__main__":
    main()
