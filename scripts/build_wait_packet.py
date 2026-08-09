#!/usr/bin/env python3
"""
Assemble the longest-wait poster packet: a cover sheet stating what the
numbers are and are not, a placement checklist with a tick box per poster, a
citywide placement map, then every poster — one PDF you can print and carry.

Called by generate_wait_posters.py; also runnable on its own against an
existing manifest:

    python scripts/build_wait_packet.py
"""

import csv
import html
import json
import math
import subprocess
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "posters" / "wait"
FIT = REPO / "data" / "analysis" / "wait_model_fit.json"
NTA_GEOJSON = REPO / "data" / "raw" / "nta2020.geojson"
RENDER = REPO / "scripts" / "render_poster.sh"

W, H = 850, 1134

CAMPAIGN_TITLE = {
    "longest_wait": "Longest waits",
    "time_lost": "Most time lost",
    "borough_no1": "Borough worst",
    "nabe_no1": "Neighbourhood worst",
}
CAMPAIGN_COLOUR = {
    "longest_wait": "#dc343b",
    "time_lost": "#111111",
    "borough_no1": "#1c7ed6",
    "nabe_no1": "#2f9e44",
}
ORDER = ["longest_wait", "time_lost", "borough_no1", "nabe_no1"]

SHEET_CSS = """
<meta charset="utf-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Red+Hat+Mono:wght@300;400;500;600;700&display=swap');
  :root {
    --ink: #111111; --ink-2: #55534e; --ink-3: #8d8a82;
    --rule: #cfccc4; --red: #dc343b; --cyan: #00c4d4;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    position: relative; width: 850px; height: 1134px; overflow: hidden;
    background: #fff; color: var(--ink);
    font-family: 'Red Hat Mono', ui-monospace, Menlo, monospace;
    font-variant-numeric: tabular-nums;
    padding: 46px 58px 40px;
    -webkit-font-smoothing: antialiased;
  }
  .grid {
    position: absolute; inset: 0; z-index: -1;
    background-image: radial-gradient(circle, #d8d5cc 0.7px, transparent 0.8px);
    background-size: 18px 18px; background-position: 4px 4px;
  }
  .head {
    display: flex; align-items: flex-end; justify-content: space-between;
    padding-bottom: 9px; border-bottom: 1.5px solid var(--ink);
    font-size: 11px; font-weight: 600; letter-spacing: 0.26em;
    text-transform: uppercase;
  }
  h1 {
    margin: 24px 0 6px; font-size: 34px; font-weight: 700;
    letter-spacing: -0.03em; line-height: 1.06;
  }
  .sub {
    font-size: 13px; font-weight: 400; line-height: 1.55; color: var(--ink-2);
    max-width: 640px;
  }
  .foot {
    position: absolute; left: 58px; right: 58px; bottom: 34px;
    display: flex; align-items: center; justify-content: space-between;
    padding-top: 9px; border-top: 1px solid var(--rule);
    font-size: 9px; font-weight: 500; letter-spacing: 0.12em; color: var(--ink-3);
  }
  .wordmark { display: flex; align-items: center; gap: 8px; }
  .wordmark b {
    display: inline-block; border: 1.3px solid var(--ink-2); padding: 0 2.5px;
    margin-right: 1.5px; font-size: 9px; font-weight: 700; line-height: 13px;
    color: var(--ink-2);
  }
  .wordmark .gap { display: inline-block; width: 5px; }
</style>
"""


def wordmark():
    return ('<span class="wordmark"><span>'
            + "".join(f"<b>{c}</b>" for c in "CITY")
            + '<span class="gap"></span>'
            + "".join(f"<b>{c}</b>" for c in "EDIT")
            + '</span><span>cityedit.org</span></span>')


def render(doc, stem, scale):
    hp, pp = OUT / f"{stem}.html", OUT / f"{stem}.png"
    hp.write_text(doc)
    subprocess.run([str(RENDER), str(hp), str(pp), str(W), str(H), str(scale)],
                   check=True, capture_output=True)
    return pp


# ------------------------------------------------------------------- cover

COVER_CSS = """
<style>
  .lede {
    margin: 22px 0 0; font-size: 19px; font-weight: 400; line-height: 1.45;
    letter-spacing: -0.005em; max-width: 660px;
  }
  .lede b { font-weight: 700; }
  .totals {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 0;
    margin: 26px 0 0; border-top: 1.5px solid var(--ink);
  }
  .totals div { padding: 12px 14px 14px 0; }
  .totals div + div { border-left: 1px solid var(--rule); padding-left: 20px; }
  .totals .n { font-size: 31px; font-weight: 700; letter-spacing: -0.03em; }
  .totals .k {
    margin-top: 8px; font-size: 9.5px; font-weight: 500; line-height: 1.5;
    letter-spacing: 0.13em; text-transform: uppercase; color: var(--ink-2);
  }
  h2 {
    margin: 28px 0 11px; font-size: 11px; font-weight: 600;
    letter-spacing: 0.22em; text-transform: uppercase; color: var(--ink-2);
    padding-bottom: 7px; border-bottom: 1px solid var(--rule);
  }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 30px; }
  .cols p {
    margin: 0 0 12px; font-size: 11.5px; font-weight: 400; line-height: 1.58;
    color: var(--ink-2);
  }
  .cols p b { color: var(--ink); font-weight: 600; }
  .src {
    margin: 0; font-size: 10.5px; line-height: 1.65; color: var(--ink-2);
    list-style: none; padding: 0;
  }
  .src li { padding-left: 15px; text-indent: -15px; }
  .src span { color: var(--ink-3); }
</style>
"""


def cover_sheet(manifest, scale):
    fit = json.loads(FIT.read_text())
    v = fit["volume_model"]
    total = fit["citywide_lost_hours_per_weekday"]
    boroughs = len({m["borough"] for m in manifest})

    doc = SHEET_CSS + COVER_CSS + f"""
<div class="grid"></div>
<div class="head"><span>City&nbsp;Edit&nbsp;/&nbsp;Crossings</span>
  <span>Packet&nbsp;02</span></div>
<h1>The longest waits<br>in New York City</h1>
<p class="lede">Every signalised corner in the five boroughs, scored by how
long a pedestrian stands there and how many pedestrians are standing there to
do it. <b>{len(manifest)} posters</b>, across {boroughs} boroughs, each with a
QR that opens that exact crossing on City Edit.</p>

<div class="totals">
  <div><div class="n">{fit['intersections']:,}</div>
    <div class="k">Signalised crossings<br>scored citywide</div></div>
  <div><div class="n">{total:,}</div>
    <div class="k">Person-hours a weekday<br>spent waiting to cross</div></div>
  <div><div class="n">{total * 250 / 1800:,.0f}</div>
    <div class="k">Full-time jobs&rsquo; worth<br>of that, every year</div></div>
</div>

<h2>What the two numbers are</h2>
<div class="cols">
  <div>
    <p><b>The wait is modelled, not measured.</b> No agency publishes New York's
    signal timing, so each crossing's wait is estimated from signal
    engineering: a 60, 90 or 120-second cycle picked from the road hierarchy;
    a green split apportioned between the two streets by lanes and class; and
    the standard uniform-arrival result for a fixed-time signal,
    d&nbsp;=&nbsp;(C&nbsp;&minus;&nbsp;g)&sup2;&nbsp;/&nbsp;2C.</p>
    <p>Divided boulevards are the reason the top of this list looks the way it
    does. Where a street reaches the crossing as several separate
    carriageways, the pedestrian waits on each island in turn, and every extra
    island costs another full expected wait.</p>
  </div>
  <div>
    <p><b>The crowd is modelled too.</b> DOT counts pedestrians at 114 places;
    95 of them are street corners. A log-linear fit on those 95 —
    distance-decayed subway entries plus DOT's own Pedestrian Mobility Plan
    demand class — predicts a corner's volume with a leave-one-out
    R&sup2;&nbsp;of&nbsp;{v['loo_r2']:.2f}, or roughly a third either way.</p>
    <p><b>Where it is weakest:</b> the count programme samples retail
    corridors, so the fit extrapolates worst on quiet streets; and subway
    access carries most of the signal, so volumes are least trustworthy far
    from the network — much of Staten Island and eastern Queens. Treat the
    ranking as a way to start the argument, not to settle it.</p>
  </div>
</div>

<h2>Built from</h2>
<ul class="src">
  <li>NYC DOT &mdash; Bi-Annual Pedestrian Counts <span>(cqsj-cfgu)</span></li>
  <li>NYC DOT &mdash; Pedestrian Mobility Plan Pedestrian Demand <span>(fwpa-qxaf)</span></li>
  <li>NYC DOT &mdash; Exclusive Pedestrian Signal locations <span>(8kuj-2n3u)</span></li>
  <li>NYC DOT &mdash; Leading Pedestrian Interval signals <span>(xc4v-ntf4)</span></li>
  <li>MTA &mdash; Subway Hourly Ridership, May 2026 <span>(5wq4-mkjj)</span></li>
  <li>OpenStreetMap &mdash; signals, carriageways, lanes and street names</li>
</ul>

<div class="foot"><span>Rebuild: scripts/build_wait_rankings.py &rarr;
  scripts/generate_wait_posters.py</span>{wordmark()}</div>
"""
    return render(doc, "cover", scale)


# --------------------------------------------------------------- checklist

CHECKLIST_CSS = """
<style>
  .cols { display: flex; gap: 26px; margin-top: 18px; }
  .col { flex: 1; min-width: 0; }
  table { border-collapse: collapse; width: 100%; }
  th {
    text-align: left; font-size: 9px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.14em; color: var(--ink-3); padding: 0 4px 5px;
    border-bottom: 1.5px solid var(--ink);
  }
  th.r, td.r { text-align: right; }
  td {
    padding: 4.5px 4px; border-bottom: 1px solid #e9e7e0; font-size: 10.5px;
    vertical-align: top;
  }
  .box {
    width: 12px; height: 12px; border: 1.5px solid var(--ink);
    display: inline-block; vertical-align: -1px;
  }
  .code { font-weight: 700; letter-spacing: 0.1em; }
  .where { display: block; font-size: 8.5px; color: var(--ink-3); margin-top: 2px; }
  .dot {
    display: inline-block; width: 7px; height: 7px; margin-right: 5px;
    vertical-align: 0;
  }
  .wait { font-weight: 600; }
  .pageno { position: absolute; right: 58px; bottom: 52px; font-size: 10px;
            color: var(--ink-3); }
  .notes { margin-top: 26px; padding-top: 13px; border-top: 1.5px solid var(--ink);
           max-width: 620px; }
  .notes h3 {
    margin: 0 0 8px; font-size: 10px; font-weight: 600; letter-spacing: 0.22em;
    text-transform: uppercase; color: var(--ink-2);
  }
  .notes p {
    margin: 0 0 7px; font-size: 11.5px; line-height: 1.55; color: var(--ink-2);
  }
</style>
"""

ROWS_PER_COL = 18


def checklist_pages(manifest, scale):
    per_page = ROWS_PER_COL * 2
    pages = [manifest[i:i + per_page] for i in range(0, len(manifest), per_page)]
    out = []

    def table(ms):
        body = "".join(
            f'<tr><td><span class="box"></span></td>'
            f'<td class="code">{m["code"]}</td>'
            f'<td><span class="dot" style="background:'
            f'{CAMPAIGN_COLOUR[m["campaign"]]}"></span>'
            f'{html.escape(m["major"])}<span class="where">at '
            f'{html.escape(m["minor"])} &middot; {html.escape(m["borough"])}</span></td>'
            f'<td class="r wait">{int(m["wait_s"]) // 60}:'
            f'{int(m["wait_s"]) % 60:02d}</td></tr>'
            for m in ms)
        return ('<table><tr><th></th><th>Code</th><th>Crossing</th>'
                f'<th class="r">Wait</th></tr>{body}</table>')

    for i, rows in enumerate(pages):
        cols = f'<div class="col">{table(rows[:ROWS_PER_COL])}</div>'
        if rows[ROWS_PER_COL:]:
            cols += f'<div class="col">{table(rows[ROWS_PER_COL:])}</div>'
        legend = " &nbsp; ".join(
            f'<span class="dot" style="background:{CAMPAIGN_COLOUR[c]}"></span>'
            f'{CAMPAIGN_TITLE[c]}'
            for c in ORDER if c in {m["campaign"] for m in manifest})
        doc = SHEET_CSS + CHECKLIST_CSS + f"""
<div class="grid"></div>
<div class="head"><span>City&nbsp;Edit&nbsp;/&nbsp;Crossings</span>
  <span>Placement&nbsp;checklist</span></div>
<h1>Where these go</h1>
<p class="sub">Each poster carries its code in the bottom-left corner. Hang it
in sight of the crossing it names, tick it off here, and the QR will bring
whoever reads it to that exact spot on the map.<br>
<span style="font-size:11px;color:var(--ink-3)">{legend}</span></p>
<div class="cols">{cols}</div>
<div class="notes">
  <h3>Before you go</h3>
  <p>Hang it at eye level, facing the crossing it names, where somebody
  already standing still can read it — the whole point is that they have
  ninety seconds to spare.</p>
  <p>Don't cover signage, signal heads or anyone else's notice. Lamp posts and
  utility boxes take tape; bus shelters and subway entrances are MTA property.</p>
  <p>Paper posters are weather, not architecture. Expect to re-hang.</p>
</div>
<div class="pageno">Checklist {i + 1} / {len(pages)}</div>
<div class="foot"><span>Wait is the modelled average, one crossing of the
  main street</span>{wordmark()}</div>
"""
        out.append(render(doc, f"checklist_{i + 1}", scale))
    return out


# -------------------------------------------------------------- placement map

def map_page(manifest, scale):
    if not NTA_GEOJSON.exists():
        print(f"!! map page skipped: {NTA_GEOJSON} missing")
        return None
    g = json.load(open(NTA_GEOJSON))
    rings = []
    for f in g["features"]:
        geom = f["geometry"]
        polys = (geom["coordinates"] if geom["type"] == "MultiPolygon"
                 else [geom["coordinates"]])
        for poly in polys:
            rings.append(poly[0])

    top, pad = 150, 58
    map_h = H - top - 130
    lons = [c[0] for r in rings for c in r]
    lats = [c[1] for r in rings for c in r]
    lon0, lon1, lat0, lat1 = min(lons), max(lons), min(lats), max(lats)
    coslat = math.cos(math.radians((lat0 + lat1) / 2))
    k = min((W - 2 * pad) / ((lon1 - lon0) * coslat), map_h / (lat1 - lat0))
    ox = (W - (lon1 - lon0) * coslat * k) / 2
    oy = top + (map_h - (lat1 - lat0) * k) / 2

    def xy(lon, lat):
        return ox + (lon - lon0) * coslat * k, oy + (lat1 - lat) * k

    paths = []
    for ring in rings:
        step = max(1, len(ring) // 110)
        pts = ring[::step]
        if len(pts) < 3:
            continue
        d = "M" + "L".join(f"{x:.1f},{y:.1f}"
                           for x, y in (xy(lo, la) for lo, la in pts)) + "Z"
        paths.append(f"<path d='{d}' fill='#f0eee7' stroke='#ffffff' "
                     f"stroke-width='0.7'/>")

    # Downtown and Midtown stack half a dozen corners inside a few hundred
    # metres, so labels get nudged clear of each other and tied back to their
    # dot with a leader line.
    placed, dots = [], []
    for m in sorted(manifest, key=lambda m: (-float(m["lat"]), float(m["lon"]))):
        x, y = xy(float(m["lon"]), float(m["lat"]))
        lx, ly = x + 6, y + 3
        while any(abs(lx - px) < 26 and abs(ly - py) < 9 for px, py in placed):
            ly += 9.5
        placed.append((lx, ly))
        c = CAMPAIGN_COLOUR[m["campaign"]]
        leader = ("" if ly - y < 6 else
                  f"<line x1='{x:.1f}' y1='{y:.1f}' x2='{lx - 1:.1f}' "
                  f"y2='{ly - 2.5:.1f}' stroke='{c}' stroke-width='0.6'/>")
        dots.append(
            f"{leader}<circle cx='{x:.1f}' cy='{y:.1f}' r='3.6' fill='{c}' "
            f"stroke='#ffffff' stroke-width='1.2'/>"
            f"<text x='{lx:.1f}' y='{ly:.1f}' style=\"font:600 7px "
            f"'Red Hat Mono',monospace;fill:#111;paint-order:stroke;"
            f"stroke:#fff;stroke-width:2.4px\">{m['code']}</text>")

    legend = "".join(
        f"<span style='display:inline-flex;align-items:center;gap:6px;"
        f"margin-right:20px'><span style='width:9px;height:9px;"
        f"background:{CAMPAIGN_COLOUR[c]};display:inline-block'></span>"
        f"{CAMPAIGN_TITLE[c]}</span>"
        for c in ORDER if c in {m["campaign"] for m in manifest})

    doc = SHEET_CSS + f"""
<div class="grid"></div>
<div class="head"><span>City&nbsp;Edit&nbsp;/&nbsp;Crossings</span>
  <span>Placement&nbsp;map</span></div>
<h1>{len(manifest)} corners</h1>
<p class="sub">One dot per poster. Codes match the checklist and the mark in
each poster's bottom-left corner.</p>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}"
     style="position:absolute;left:0;top:0;z-index:-1">
  {''.join(paths)}{''.join(dots)}
</svg>
<div style="position:absolute;left:58px;bottom:74px;font-size:10.5px;
     color:var(--ink-2)">{legend}</div>
<div class="foot"><span></span>{wordmark()}</div>
"""
    return render(doc, "map_page", scale)


# ------------------------------------------------------------------- packet

def build_packet(manifest, scale=2):
    from PIL import Image

    sheets = [cover_sheet(manifest, scale)]
    sheets += checklist_pages(manifest, scale)
    mp = map_page(manifest, scale)
    if mp:
        sheets.append(mp)

    pages = [Image.open(p).convert("RGB") for p in sheets]
    pages += [Image.open(REPO / m["png"]).convert("RGB") for m in manifest]
    pdf = OUT / "wait-poster-book.pdf"
    pages[0].save(pdf, save_all=True, append_images=pages[1:],
                  resolution=int(72 * scale), quality=88)
    print(f"packet: {pdf} ({len(pages)} pages — {len(sheets)} front matter "
          f"+ {len(manifest)} posters)")
    return pdf


def main():
    rows = list(csv.DictReader(open(OUT / "manifest.csv")))
    for r in rows:
        for k in ("lat", "lon", "wait_s", "peds_day", "lost_hours_day"):
            r[k] = float(r[k])
    build_packet(rows)


if __name__ == "__main__":
    main()
