#!/usr/bin/env python3
"""
Build the "longest wait" poster packet: one poster per intersection, each
carrying a QR that opens that exact crossing on City Edit with "Fix signal
timing" already selected, plus a placement checklist, a placement map and an
assembled PDF.

Input:   data/analysis/wait_intersections.csv  (build_wait_rankings.py)
Template: data/posters/templates/longest_wait.html
Output:  data/posters/wait/…  and  data/posters/wait/wait-poster-book.pdf

Campaigns — each intersection is claimed once, in this order:

  longest_wait   the longest estimated waits in the city, among corners busy
                 enough that the wait is actually being served to people
  time_lost      the corners where wait x crowd burns the most human time
  borough_no1    the worst in each of the five boroughs, so the packet is not
                 four dozen posters about Manhattan
  nabe_no1       the worst in each of N neighbourhoods, for spread

Usage:
  python scripts/generate_wait_posters.py
  python scripts/generate_wait_posters.py --campaign longest_wait --limit 5
"""

import argparse
import csv
import html
import json
import math
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote_plus

import qrcode

REPO = Path(__file__).resolve().parent.parent
MASTER = REPO / "data" / "analysis" / "wait_intersections.csv"
FIT = REPO / "data" / "analysis" / "wait_model_fit.json"
TEMPLATE = REPO / "data" / "posters" / "templates" / "longest_wait.html"
NTA_GEOJSON = REPO / "data" / "raw" / "nta2020.geojson"
OUT = REPO / "data" / "posters" / "wait"
RENDER = REPO / "scripts" / "render_poster.sh"

BASE_URL = "https://cityedit.org/m/nyc-proposals"
QR_VOTE_TYPE = "Fix signal timing"
QR_SRC = "qr-wait"

# A corner has to actually serve people before "the longest wait in New York"
# means anything. Below this the wait is real but nobody is standing in it.
MIN_PEDS_FOR_WAIT_CLAIM = 4000

WEEKDAYS_PER_YEAR = 250
FULL_TIME_YEAR_HOURS = 1800.0   # a 35-hour week with holidays

NUMERIC = ("lat", "lon", "wait_major_s", "wait_minor_s", "peds_day",
           "peds_cross_major", "peds_cross_minor", "lost_hours_day",
           "cycle_s", "green_s", "cross_m", "major_lanes", "minor_lanes")
INTS = ("stages", "nodes", "rank_lost", "rank_wait", "barnes", "lpi")


def load_rows():
    rows = list(csv.DictReader(open(MASTER)))
    for r in rows:
        for k in NUMERIC:
            r[k] = float(r[k])
        for k in INTS:
            r[k] = int(r[k])
    return rows


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


def mmss(seconds):
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def fill(doc, slot_id, content):
    """Replace the inner HTML of the element carrying id=slot_id.

    Scans for the matching close tag rather than the first one — the chart and
    stats slots hold nested divs, and a non-greedy regex would close them at
    the first child and silently shred the layout."""
    open_re = re.compile(r'<(\w+)[^>]*\bid="' + re.escape(slot_id) + r'"[^>]*>')
    m = open_re.search(doc)
    if not m:
        raise KeyError(f"template has no slot #{slot_id}")
    tag = m.group(1)
    depth, pos = 1, m.end()
    step = re.compile(rf'<(/?){tag}\b[^>]*>')
    while depth:
        s = step.search(doc, pos)
        if not s:
            raise ValueError(f"slot #{slot_id} is never closed")
        depth += -1 if s.group(1) else 1
        pos = s.end()
        close_start = s.start()
    return doc[:m.end()] + content + doc[close_start:]


def set_src(doc, slot_id, value):
    return re.sub(r'(<img[^>]*\bid="' + slot_id + r'"[^>]*\bsrc=")[^"]*(")',
                  lambda m: m.group(1) + value + m.group(2), doc, count=1)


# ------------------------------------------------------------ the crossing

# The signal diagram is the argument the poster is making, so it is drawn from
# the same numbers the ranking used: the cycle, the green the model gives this
# crossing, and how many carriageways stand between the two kerbs.
BAR_H = {1: 96, 2: 56, 3: 40, 4: 30}     # keep the block near a constant height
BAR_GAP = {1: 0, 2: 18, 3: 14, 4: 12}


def crossing_chart(r):
    """Plan view and timing chart, sharing one row per carriageway."""
    stages = max(1, min(4, r["stages"]))
    h, gap = BAR_H[stages], BAR_GAP[stages]
    total = stages * h + (stages - 1) * gap
    cycle = r["cycle_s"]
    green = r["green_s"]
    # The WALK indication is the only part of the cycle you may *start* on;
    # the rest of the green is the countdown, for finishing.
    walk = min(7.0, green)
    flash = max(0.0, green - walk)
    walk_pct = 100.0 * walk / cycle
    flash_pct = 100.0 * flash / cycle

    bars = []
    for i in range(stages):
        lab = f'<span class="lab">{i + 1}</span>' if stages > 1 else ""
        bars.append(
            f'<div class="bar" style="height:{h}px">'
            f'<span class="seg walk" style="left:0;width:{walk_pct:.2f}%"></span>'
            f'<span class="seg flash" style="left:{walk_pct:.2f}%;'
            f'width:{flash_pct:.2f}%"></span>{lab}</div>')
    bars_html = f'<div class="bars" style="gap:{gap}px">{"".join(bars)}</div>'

    # Plan view: each carriageway as a band whose width is its share of the
    # road, the pedestrian's path straight down the middle, a dot on each
    # island. Lane counts are not stored per carriageway, so the bands share
    # the road evenly except that a wider street gets wider bands.
    w = 100
    per = r["major_lanes"] / stages
    band_w = max(52.0, min(92.0, 40 + per * 12))
    parts = [f'<svg class="plan" width="{w}" height="{total}" '
             f'viewBox="0 0 {w} {total}">']
    for i in range(stages):
        y = i * (h + gap)
        x = (w - band_w) / 2
        parts.append(
            f'<rect x="{x:.1f}" y="{y}" width="{band_w:.1f}" height="{h}" '
            f'fill="#edebe4" stroke="#8d8a82" stroke-width="1"/>'
            f'<line x1="{x + 4:.1f}" y1="{y + h / 2:.1f}" '
            f'x2="{x + band_w - 4:.1f}" y2="{y + h / 2:.1f}" '
            f'stroke="#ffffff" stroke-width="1.5" stroke-dasharray="5 5"/>')
    parts.append(
        f'<line x1="{w / 2}" y1="0" x2="{w / 2}" y2="{total}" '
        f'stroke="#dc343b" stroke-width="1.5" stroke-dasharray="4 4"/>')
    for i in range(stages - 1):
        cy = (i + 1) * (h + gap) - gap / 2
        parts.append(f'<circle cx="{w / 2}" cy="{cy:.1f}" r="3.6" fill="#ffffff" '
                     f'stroke="#dc343b" stroke-width="1.5"/>')
    parts.append("</svg>")

    ticks = []
    step = 30 if cycle >= 90 else 15
    n = int(cycle // step)
    for k in range(n + 1):
        t = k * step
        pct = 100.0 * t / cycle
        cls = "first" if k == 0 else ("last" if k == n else "")
        ticks.append(f'<span class="{cls}" style="left:{pct:.1f}%">{t:g}s</span>')
    axis = f'<div class="axis">{"".join(ticks)}</div>'

    islands = stages - 1
    note = (f'{r["cross_m"]:.0f} m of road'
            + (f'<br>{islands} island{"s" if islands != 1 else ""}'
               if islands else '<br>no island'))
    return ("".join(parts) + bars_html
            + f'<div id="plan-note">{note}</div>' + axis)


def site_notes(r):
    """Facts about this particular signal, stated plainly. The red ones are
    the ones a passer-by can ask the city to change."""
    chips = [f'{r["cycle_s"]:.0f} s cycle']
    if r["stages"] > 1:
        chips.append(f'{r["stages"]}-stage crossing')
    chips.append(f'{r["cross_m"]:.0f} m kerb to kerb')
    if r["barnes"]:
        chips.append("Exclusive pedestrian phase")
    warn = []
    if not r["lpi"]:
        warn.append("No leading pedestrian interval")
    if r["cycle_s"] >= 120:
        warn.append("Longest cycle the city runs")
    return "".join(f"<span>{c}</span>" for c in chips) + \
           "".join(f'<span class="warn">{c}</span>' for c in warn)


def diagram_title(r):
    stages = max(1, min(4, r["stages"]))
    words = {1: "One crossing", 2: "Two crossings", 3: "Three crossings",
             4: "Four crossings"}[stages]
    each = " each" if stages > 1 else ""
    return f'{words} &middot; a {r["cycle_s"]:.0f}-second cycle{each}'


# ------------------------------------------------------------- locator map

_CITY_PATH = None


def city_path(size):
    """One simplified silhouette of the five boroughs, projected once and
    reused by every poster; only the dot moves."""
    global _CITY_PATH
    if _CITY_PATH and _CITY_PATH[0] == size:
        return _CITY_PATH[1], _CITY_PATH[2]

    g = json.load(open(NTA_GEOJSON))
    rings = []
    for f in g["features"]:
        geom = f["geometry"]
        polys = (geom["coordinates"] if geom["type"] == "MultiPolygon"
                 else [geom["coordinates"]])
        for poly in polys:
            rings.append(poly[0])
    lons = [c[0] for r in rings for c in r]
    lats = [c[1] for r in rings for c in r]
    lon0, lon1, lat0, lat1 = min(lons), max(lons), min(lats), max(lats)
    coslat = math.cos(math.radians((lat0 + lat1) / 2))
    pad = 4
    k = min((size - 2 * pad) / ((lon1 - lon0) * coslat),
            (size - 2 * pad) / (lat1 - lat0))
    ox = (size - (lon1 - lon0) * coslat * k) / 2
    oy = (size - (lat1 - lat0) * k) / 2

    def xy(lon, lat):
        return ox + (lon - lon0) * coslat * k, oy + (lat1 - lat) * k

    paths = []
    for ring in rings:
        step = max(1, len(ring) // 60)
        pts = ring[::step]
        if len(pts) < 3:
            continue
        d = "M" + "L".join(f"{x:.1f},{y:.1f}"
                           for x, y in (xy(lo, la) for lo, la in pts)) + "Z"
        paths.append(d)
    _CITY_PATH = (size, paths, xy)
    return paths, xy


def locator(r, size=168):
    paths, xy = city_path(size)
    x, y = xy(r["lon"], r["lat"])
    body = "".join(f'<path d="{d}" fill="#e4e0d7" stroke="none"/>'
                   for d in paths)
    cross = (f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{size}" '
             f'stroke="#dc343b" stroke-width="0.7" stroke-dasharray="3 3"/>'
             f'<line x1="0" y1="{y:.1f}" x2="{size}" y2="{y:.1f}" '
             f'stroke="#dc343b" stroke-width="0.7" stroke-dasharray="3 3"/>'
             f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#dc343b"/>'
             f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="none" '
             f'stroke="#dc343b" stroke-width="1.2"/>')
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f'{body}{cross}</svg>')


# ---------------------------------------------------------------- campaigns

NTA_DISPLAY = {
    "Midtown South-Flatiron-Union Square": "Midtown South",
    "Midtown-Times Square": "Midtown",
    "Chelsea-Hudson Yards": "Chelsea",
    "Upper East Side-Lenox Hill-Roosevelt Island": "Lenox Hill",
    "Upper West Side-Manhattan Valley": "Manhattan Valley",
    "Bedford-Stuyvesant (East)": "Bed-Stuy",
    "Bedford-Stuyvesant (West)": "Bed-Stuy",
    "Astoria (North)-Ditmars-Steinway": "Ditmars",
    "Murray Hill-Kips Bay": "Kips Bay",
    "University Heights (North)-Fordham": "Fordham",
    "Mott Haven-Port Morris": "Mott Haven",
    "Concourse-Concourse Village": "Concourse",
    "East New York-New Lots": "East New York",
    "Hamilton Heights-Sugar Hill": "Hamilton Heights",
    "Sunnyside Yards (North)": "Sunnyside",
    "Financial District-Battery Park City": "the Financial District",
    "SoHo-Little Italy-Hudson Square": "SoHo",
    "Greenwich Village": "the Village",
    "West Village": "the West Village",
    "East Village": "the East Village",
    "Lower East Side": "the Lower East Side",
    "Upper West Side (Central)": "the Upper West Side",
    "Upper East Side-Carnegie Hill": "Carnegie Hill",
}
THE_NABES = {"Upper West Side", "Upper East Side", "Lower East Side",
             "East Village", "West Village", "Financial District", "Rockaways"}


def nabe(nta):
    name = NTA_DISPLAY.get(nta) or re.sub(r"\s*\([^)]*\)", "", nta).strip()
    return ("the " + name) if name in THE_NABES else name


ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}


def ordinal(n):
    return ORDINAL.get(n, f"{n}th")


def hero_wait(r):
    return {
        "figure": mmss(r["wait_major_s"]),
        "unit": "Average wait to cross,<br>every single time",
    }


def hero_hours(r):
    return {
        "figure": f'{r["lost_hours_day"]:,.0f}',
        "unit": "Hours of this city's day,<br>spent standing at<br>one corner",
    }


CAMPAIGNS = {
    # No ordinal claim here. The wait model saturates at its four-stage
    # ceiling, so a dozen boulevard crossings score identically and "the 3rd
    # longest wait in New York" would be a coin toss dressed as a fact. The
    # set is real; the order inside it is not.
    "longest_wait": {
        "title": "Longest wait citywide",
        "default_n": 10,
        "claim": "rank",
        "caps": {"major": 2},
        "chip": lambda r, n: "Longest&nbsp;waits",
        "select": lambda rows: [r for r in sorted(rows, key=lambda r: -r["wait_major_s"])
                                if r["peds_day"] >= MIN_PEDS_FOR_WAIT_CLAIM],
        "hero": hero_wait,
        # No count either: the corridor cap means these are drawn from the
        # top tier, not the literal top ten.
        "headline": lambda r, n:
            "One of the <b>longest waits</b> of any crossing in New York City",
    },
    # Person-hours is continuous and unties cleanly, so this one does claim a
    # rank.
    "time_lost": {
        "title": "Most time burnt citywide",
        "default_n": 10,
        "claim": "rank",
        "chip": lambda r, n: f"No.&nbsp;{n:02d}",
        "caps": {"major": 2},
        "select": lambda rows: sorted(rows, key=lambda r: -r["lost_hours_day"]),
        "hero": hero_hours,
        "headline": lambda r, n: (
            "The <b>biggest waste</b> of pedestrian time in New York City"
            if n == 1 else
            f"The <b>{ordinal(n)} biggest waste</b> of pedestrian time "
            f"in New York City"),
    },
    "borough_no1": {
        "title": "Worst in the borough",
        "default_n": 5,
        "claim": "unique",
        "chip": lambda r, n: html.escape(r["borough"]).replace(" ", "&nbsp;"),
        "select": None,
        "hero": hero_hours,
        "headline": lambda r, n: (
            f"The <b>single biggest waste</b> of pedestrian time in "
            f"{r['borough']}"),
    },
    "nabe_no1": {
        "title": "Worst in the neighbourhood",
        "default_n": 14,
        "claim": "unique",
        "chip": lambda r, n: html.escape(nabe(r["nta"])).replace(" ", "&nbsp;"),
        "caps": {"major": 1, "borough": 4},
        "select": None,
        "hero": hero_wait,
        "headline": lambda r, n: f"The <b>longest wait</b> in {nabe(r['nta'])}",
    },
}
ORDER = ["longest_wait", "time_lost", "borough_no1", "nabe_no1"]


def borough_targets(rows):
    best = {}
    for r in rows:
        cur = best.get(r["borough"])
        if cur is None or r["lost_hours_day"] > cur["lost_hours_day"]:
            best[r["borough"]] = r
    return sorted(best.values(), key=lambda r: -r["lost_hours_day"])


def nabe_targets(rows, n):
    """The longest wait in each neighbourhood, but only where enough people
    meet it — otherwise this campaign posts to empty sidewalks."""
    best = {}
    for r in rows:
        if not r["nta"] or r["peds_day"] < 2500:
            continue
        key = nabe(r["nta"])
        cur = best.get(key)
        if cur is None or r["wait_major_s"] > cur["wait_major_s"]:
            best[key] = r
    return sorted(best.values(), key=lambda r: -r["lost_hours_day"])[:n]


# ------------------------------------------------------------------ posters

def code_allocator():
    n = 0

    def nxt():
        nonlocal n
        code = f"{chr(65 + n // 9)}{n % 9 + 1}"
        n += 1
        return code
    return nxt


def stat_cells(r):
    cycle = int(r["cycle_s"])
    walk = min(7, int(r["green_s"]))
    years = r["lost_hours_day"] * WEEKDAYS_PER_YEAR / FULL_TIME_YEAR_HOURS
    return (
        f'<div><div class="n">{r["peds_day"]:,.0f}</div>'
        f'<div class="k">People cross<br>here each day</div></div>'
        f'<div><div class="n">{walk} sec</div>'
        f'<div class="k">Of every {cycle} you<br>may start walking</div></div>'
        f'<div><div class="n">{r["lost_hours_day"]:,.0f} hrs</div>'
        f'<div class="k">Of their time, lost<br>here every weekday</div></div>'
    ), years


def year_line(r, years):
    """The number that makes a daily figure land: a weekday habit, compounded
    over a working year."""
    return (
        f'Standing at this one corner adds up to <b>{years:,.0f} full-time '
        f'jobs&rsquo; worth</b> of doing nothing, every year.')


def build_poster(tpl, r, camp, rank, code, outdir, tag, scale, base_url, src):
    url = (f'{base_url}?w={r["lat"]:.6f},{r["lon"]:.6f}'
           f'&vt={quote_plus(QR_VOTE_TYPE)}&src={src}')
    qr_png = outdir / f"{tag}_qr.png"
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       border=1, box_size=12)
    qr.add_data(url)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(qr_png)

    hero = camp["hero"](r)
    stats, years = stat_cells(r)
    doc = tpl
    doc = fill(doc, "rank", camp["chip"](r, rank))
    doc = fill(doc, "claim", camp["headline"](r, rank))
    doc = fill(doc, "figure", hero["figure"])
    doc = fill(doc, "unit", hero["unit"])
    doc = fill(doc, "street", html.escape(r["major"]))
    doc = fill(doc, "cross", f'<em>at</em> {html.escape(r["minor"])} '
                             f'&middot; {html.escape(nabe(r["nta"]))}, '
                             f'{html.escape(r["borough"])}')
    doc = fill(doc, "diagram-title", diagram_title(r))
    doc = fill(doc, "chart", crossing_chart(r))
    doc = fill(doc, "notes", site_notes(r))
    doc = fill(doc, "stats", stats)
    doc = fill(doc, "yearline", year_line(r, years))
    doc = fill(doc, "locator", locator(r))
    doc = fill(doc, "code", code)
    doc = set_src(doc, "qr", qr_png.name)

    html_path = outdir / f"{tag}.html"
    png_path = outdir / f"{tag}.png"
    html_path.write_text(doc)
    subprocess.run([str(RENDER), str(html_path), str(png_path),
                    "850", "1134", str(scale)], check=True, capture_output=True)
    return url, png_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", choices=list(CAMPAIGNS), action="append")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--src", default=QR_SRC)
    ap.add_argument("--pdf", action="store_true", default=True)
    ap.add_argument("--no-pdf", dest="pdf", action="store_false")
    args = ap.parse_args()

    rows = load_rows()
    tpl = TEMPLATE.read_text()
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    next_code = code_allocator()
    used = set()

    for name in [c for c in ORDER if not args.campaign or c in args.campaign]:
        camp = CAMPAIGNS[name]
        n = args.limit or camp["default_n"]
        if name == "borough_no1":
            cands = borough_targets(rows)
        elif name == "nabe_no1":
            cands = nabe_targets(rows, n * 3)
        else:
            cands = camp["select"](rows)

        # Left alone, "the longest waits in New York" is nine posters about
        # Queens Boulevard — true, and a bad campaign. The caps spread the
        # same claim across the corridors and boroughs that share it.
        caps = camp.get("caps", {})
        per_major, per_boro = defaultdict(int), defaultdict(int)
        targets = []
        for rank, r in enumerate(cands, start=1):
            if len(targets) >= n:
                break
            key = (r["lat"], r["lon"])
            if key in used:
                # A "#N" claim can't be handed to a runner-up, and a "worst in
                # X" claim can't either — both campaigns just skip.
                print(f"   {name}: already claimed, dropped {r['name']}")
                continue
            if per_major[r["major"]] >= caps.get("major", 99):
                continue
            if per_boro[r["borough"]] >= caps.get("borough", 99):
                continue
            per_major[r["major"]] += 1
            per_boro[r["borough"]] += 1
            targets.append((r, rank if camp["claim"] == "rank" else len(targets) + 1))

        outdir = OUT / name
        outdir.mkdir(parents=True, exist_ok=True)
        for r, rank in targets:
            used.add((r["lat"], r["lon"]))
            code = next_code()
            tag = f"{rank:02d}_{slug(r['name'])}"
            url, png = build_poster(tpl, r, camp, rank, code, outdir, tag,
                                    args.scale, args.base_url, args.src)
            manifest.append({
                "code": code, "campaign": name, "rank": rank,
                "name": r["name"], "major": r["major"], "minor": r["minor"],
                "nta": r["nta"], "borough": r["borough"],
                "lat": r["lat"], "lon": r["lon"],
                "wait_s": r["wait_major_s"], "peds_day": r["peds_day"],
                "lost_hours_day": r["lost_hours_day"], "stages": r["stages"],
                "url": url, "png": str(png.relative_to(REPO)),
            })
            print(f"{code}: {r['name']} — {mmss(r['wait_major_s'])} wait, "
                  f"{r['lost_hours_day']:,.0f} h/day lost")

    if not manifest:
        print("nothing selected")
        return
    with open(OUT / "manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0].keys()))
        w.writeheader()
        w.writerows(manifest)
    print(f"\n{len(manifest)} posters -> {OUT}")

    if args.pdf:
        from build_wait_packet import build_packet
        build_packet(manifest, args.scale)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
