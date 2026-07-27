#!/usr/bin/env python3
"""Changelog report for the 2026-07-27 dangerous-intersections + posters workstream.

Reuses the rendering machinery from build_report.py, overriding the content.
Run from repo root: python changelog/build_report_intersections.py
(The diff is captured in changelog/changes-intersections.diff; the huge
data-URI poster templates are deliberately excluded from it and described
in prose instead.)
"""
import html
import importlib.util
import os

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("base", os.path.join(HERE, "build_report.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

base.DIFF_PATH = os.path.join(HERE, "changes-intersections.diff")
base.OUT_PATH = os.path.join(HERE, "2026-07-27-dangerous-intersections-posters.html")
base.DATE = "2026-07-27"
base.TITLE = "Fresh dangerous-intersections analysis + procedural QR posters"

base.SECTIONS = [
    {
        "id": "analysis",
        "tag": "Data pipeline",
        "title": "1 · Re-ranking NYC's dangerous intersections on fresh data",
        "symptom": (
            "The existing <em>NYC Intersection Data Analysis</em> PDF pooled 3½ years of crashes "
            "(2023 → mid-2026). Pooled rankings go stale: the city responds to exactly this data, so an "
            "intersection can top the all-time list years after a redesign fixed it. We needed rankings "
            "built from the <strong>latest danger indicators</strong>, under several definitions of danger, "
            "to target a poster campaign."
        ),
        "cause": [
            "Re-ranked on the last 12 months only, just 3 of the PDF's top 20 stay in the citywide top 50. "
            "Chrystie St / Grand St (#7 all-time) fell to #504 (3 crashes vs a ~8/yr baseline); "
            "8th Ave / W 57th St fell to #1048. The stale-ranking concern is measurable, not hypothetical.",
            "Crashes are snapped to intersections in <strong>City Edit's own NYC walk graph</strong> "
            "(nodes where ≥2 distinct street names meet, 56k of them) rather than a third-party "
            "intersection layer — so every ranked intersection deep-links into the app: "
            "<code>cityedit.org/m/nyc-walkways?w=&lt;lat&gt;,&lt;lon&gt;</code>.",
        ],
        "fixes": [
            "<strong>scripts/build_intersection_rankings.py</strong> — pulls stay out of the script "
            "(<code>data/raw/crashes_pedcyc_2023on.csv</code>, 47,965 rows from Socrata h9gi-nx95, data "
            "through 2026-06-11); snaps 40,103 crashes to 16,253 intersections (40 m grid index); tags "
            "each with its NTA-2020 neighborhood (shapely point-in-polygon).",
            "Six danger definitions per intersection: chronic count (the PDF's), fresh-12-month "
            "severity-weighted (<code>injured + 8×killed</code>), recency-decayed (12-month half-life — the "
            "recommended poster metric), pedestrian/cyclist-specific variants, Poisson trend surprise "
            "(heating-up / cooling-down, with a ½-crash Jeffreys prior so zero-baseline hotspots stay "
            "finite), and night share (9pm–6am).",
            "Outputs: <code>data/analysis/intersections_master.csv</code> + 11 top-25 lists + per-NTA #1s "
            "(184 neighborhoods).",
            "<strong>data/analysis/report.html</strong> — self-contained audit report (PDF-top-20 movement "
            "table, definition tabs, then-vs-now bars, baseline-vs-fresh scatter with outlier classes, "
            "score distribution, poster targeting tiers, methods + caveats). Published as a private "
            "artifact; charts follow the dataviz palette rules (validated).",
        ],
        "files": [
            "scripts/build_intersection_rankings.py — NEW: snap + rank pipeline",
            "data/analysis/*.csv — master + per-definition top lists",
            "data/analysis/report_body.html / report.html — the audit report (report.html = body + inlined data)",
        ],
    },
    {
        "id": "posters",
        "tag": "Posters · ultracode",
        "title": "2 · Poster replication + procedural generator",
        "symptom": (
            "The PDF ships four poster mock-ups (bike “killer!”, pedestrian #1, cyclist #1, dark "
            "“kick your fear into gear”) that existed only as flat images. The campaign needs them as "
            "<strong>parameterized templates</strong> — per-intersection text + QR — plus new designs for the "
            "novel danger definitions."
        ),
        "cause": [
            "A 46-agent workflow replicated each poster as self-contained HTML: per poster, two parallel "
            "builders (font/SVG-purist vs collage-from-PDF-cuts), an adversarial design judge picking the "
            "winner and listing concrete discrepancies, then up to three refine-and-rejudge rounds. Final "
            "judge scores 80–88/100; winners were then picked by eye across all rounds (judges oscillate).",
            "Typography was rebuilt with real fonts (Rubik Dirt, Luckiest Guy, Averia Serif Libre, "
            "Rozha One, Poppins); complex artwork (bike line-drawing, car-hits-pedestrian diamond, red "
            "bike + winding road, chainring) was cut from the 200 dpi PDF renders and inlined as data URIs.",
            "Three novel siblings in the family style: <em>heating up</em> (trend outlier, rising hand-drawn "
            "chart + thermometer), <em>neighborhood #1</em> (the PDF's own “Alternatively: Melrose / "
            "Bushwick / Harlem” idea), and <em>after dark</em> (streetlight cone + moon, night-share stat).",
        ],
        "fixes": [
            "<strong>data/posters/templates/*.html</strong> — 7 self-contained templates with substitution "
            "slots (<code>id=\"headline\" / statline / cta / neighborhood / qr</code>).",
            "<strong>scripts/generate_posters.py</strong> — feeds the master CSV through 7 campaigns "
            "(ped #N, cyc #N, overall, dark, per-NTA #1, heating-up, after-dark), generates a per-"
            "intersection QR deep link, fills slots (rank posters swap only the “#1” text to keep the "
            "nested per-line markup intact; long NTA names auto-shrink), renders print-res PNGs "
            "(1700×2268 @2x) via chrome-headless-shell. Default batch: 94 posters + manifest.csv.",
            "<strong>scripts/render_poster.sh</strong> — the shared HTML→PNG renderer (also used to "
            "verify the report).",
        ],
        "files": [
            "data/posters/templates/ — 7 poster templates (4 replicas + 3 novel)",
            "data/posters/reference/ — the four PDF poster crops (comparison baseline)",
            "scripts/generate_posters.py — NEW: campaign generator (QR + slots + render)",
            "scripts/render_poster.sh — NEW: chrome-headless-shell wrapper",
            "data/posters/out/ — generated posters (gitignored; manifest.csv records name/coords/URL)",
        ],
    },
]

base.VERIFY = [
    "Rankings reproduce the PDF baseline: the full-window top list contains the PDF's top intersections "
    "(8th Ave/W 42nd #1, Morris/E 149th, Utica/Eastern Pkwy, Chrystie/Grand …) from the fresh pull.",
    "<code>generate_posters.py</code> full run: 94/94 posters rendered, no missing-template or "
    "missing-font failures; manifest.csv URLs spot-checked against master CSV coordinates.",
    "Replica renders visually compared side-by-side against the PDF crops (judge-verified, then "
    "hand-picked best round per poster).",
    "Report rendered headless in light and dark themes; chart palette passed the dataviz validator.",
]

base.CHECKLIST = [
    "Open <code>data/analysis/report.html</code> (or the published artifact) — check the PDF-movement "
    "table reads correctly and the tabs switch.",
    "Scan a QR from any PNG under <code>data/posters/out/</code> with a phone — it should open "
    "cityedit.org with that exact intersection selected (verify the pin lands on the named corner).",
    "Flip through <code>data/posters/out/ped_no1/</code> — ranks #1–#10 should read “THE #1…#10 MOST "
    "DANGEROUS” with the pin text intact.",
    "Check <code>out/nabe_no1/</code> for the longest neighborhood names (Astoria-Ditmars-Steinway) — "
    "the red name should shrink to fit, not overflow.",
    "Re-run <code>python scripts/build_intersection_rankings.py</code> after a fresh data pull next "
    "month and regenerate posters — rankings and posters should update with no manual steps.",
]

base.FILE_CONTEXT = {
    "scripts/build_intersection_rankings.py": {
        "on": ["Flask API"],
        "module": ("Analysis pipeline · scripts/", "offline data tooling next to the app (graph builders, exports, deploy helpers)"),
        "file": ("build_intersection_rankings.py", "NEW ~300 LOC — crash→intersection snap + six danger rankings"),
        "outline": [
            ("Constants / windows", "data-end 2026-06-11, fresh-12/24, baseline, K_WEIGHT=8, half-life 12mo", True),
            ("load_intersection_nodes", "walk-graph nodes with ≥2 distinct street names + display name", True),
            ("GridIndex", "~50m lat/lon buckets for nearest-intersection snaps", True),
            ("Poisson helpers", "sf/cdf for trend surprise", True),
            ("main — snap loop", "per-crash aggregation into windows, recency decay, night share", True),
            ("main — NTA join + score rows", "shapely point-in-polygon, per-row metrics", True),
            ("main — exports", "master CSV + 11 top lists + per-NTA #1s", True),
        ],
        "blocks": ["Whole file is new — the analysis pipeline behind the report and the posters."],
    },
    "scripts/generate_posters.py": {
        "on": ["React / Leaflet client"],
        "module": ("Poster tooling · scripts/", "campaign generator that turns rankings into printable QR posters"),
        "file": ("generate_posters.py", "NEW ~230 LOC — 7 campaigns × targets → filled templates → PNGs"),
        "outline": [
            ("fill_slot / set_style / set_qr", "regex slot substitution on leaf template elements", True),
            ("victims_line / hurt_ratio / night_fraction", "honest stat-line copywriting from the CSV", True),
            ("CAMPAIGNS", "template + target selector + fill/swap rules per danger definition", True),
            ("nabe_targets", "one #1 per NTA neighborhood (score ≥ 3)", True),
            ("main", "QR per intersection (colorway-matched), render via render_poster.sh, manifest.csv", True),
        ],
        "blocks": ["Whole file is new — the procedural poster generator."],
    },
    "scripts/render_poster.sh": {
        "on": ["React / Leaflet client"],
        "module": ("Poster tooling · scripts/", "shared HTML→PNG renderer"),
        "file": ("render_poster.sh", "NEW 15 LOC — chrome-headless-shell screenshot wrapper (size + device-scale)"),
        "outline": [("render", "window-size, force-device-scale-factor, virtual-time-budget for webfonts", True)],
        "blocks": ["Whole file is new."],
    },
    "data/analysis/report_body.html": {
        "on": ["React / Leaflet client"],
        "module": ("Analysis deliverables · data/analysis/", "the audit report + ranked CSVs consumed by the poster generator"),
        "file": ("report_body.html", "NEW ~640 LOC — report template; report.html = this + inlined report_data.json"),
        "outline": [
            ("Design tokens", "light/dark palettes, danger-audit identity", True),
            ("Lead + stat band", "freshness framing, last-12-months totals", True),
            ("§1 PDF movement table", "still hot / fading / cooled verdicts per PDF-top-20 row", True),
            ("§2 definition cards + tabs", "six definitions, top-10 tables per definition", True),
            ("§3 outlier charts", "then-vs-now bars, baseline-vs-fresh scatter, score distribution", True),
            ("§4 poster targeting tiers", "citywide #1s / neighborhood #1s / heating-up / after-dark", True),
            ("§5 methods & caveats", "join stats, weight dials, no exposure denominator, data lag", True),
        ],
        "blocks": ["Whole file is new — the report the posters campaign is planned from."],
    },
}


def patched_section_html(s):
    return f"""
    <section class="card" id="{s['id']}">
      <div class="tag">{s['tag']}</div>
      <h2>{s['title']}</h2>
      <h3>Context</h3>
      <p>{s['symptom']}</p>
      <h3>What the data showed / how it was built</h3>
      <ul>{base.li(s['cause'])}</ul>
      <h3>What was built</h3>
      <ul>{base.li(s['fixes'])}</ul>
      <h3>Files touched</h3>
      <ul class="files">{base.li(html.escape(f) for f in s['files'])}</ul>
    </section>
    """


base.section_html = patched_section_html

if __name__ == "__main__":
    base.main()
    # base.main()'s page shell hardcodes the old workstream's branch + lede;
    # patch them in the emitted HTML.
    with open(base.OUT_PATH) as f:
        doc = f.read()
    doc = doc.replace(
        "branch <code>fix/unify-voting</code>", "branch <code>main</code>"
    ).replace(
        "Three fixes in one pass: the stale-cache crash that loops mobile Safari on heatmap load,\n"
        "  the concurrency limits that broke the app under multiple tenants, and the heatmap that vanished mid-zoom.",
        "Fresh crash data, six definitions of “dangerous,” and a poster factory: the 2023-on PDF analysis "
        "re-run on data through June 2026 (the old top-20 is measurably stale), plus its four poster "
        "mock-ups replicated as parameterized templates and 94 QR posters generated procedurally.",
    )
    with open(base.OUT_PATH, "w") as f:
        f.write(doc)
    print("patched lede + branch")
