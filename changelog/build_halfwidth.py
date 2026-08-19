#!/usr/bin/env python3
"""Generate the HTML changelog report from the captured unified diff.

Run from repo root: python changelog/build_report.py
Reads changelog/changes-halfwidth.diff, writes changelog/2026-08-19-halfwidth-floating-bar.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-halfwidth.diff")
OUT_PATH = os.path.join(HERE, "2026-08-19-halfwidth-floating-bar.html")
SHOT_DIR = os.environ.get("SHOT_DIR", "")

DATE = "2026-08-19"
TITLE = "Half width: the empty plate before the minus"


def split_by_file(diff_text: str):
    """Split a unified diff into (filename, hunk_text) chunks."""
    files = []
    current_name = None
    current_lines: list[str] = []
    for line in diff_text.splitlines():
        m = re.match(r"^diff --git a/(\S+) b/(\S+)", line)
        if m:
            if current_name is not None:
                files.append((current_name, "\n".join(current_lines)))
            current_name = m.group(2)
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_name is not None:
        files.append((current_name, "\n".join(current_lines)))
    return files


def colorize(diff_chunk: str) -> str:
    out = []
    for raw in diff_chunk.splitlines():
        esc = html.escape(raw)
        if raw.startswith("+++") or raw.startswith("---"):
            cls = "d-meta"
        elif raw.startswith("@@"):
            cls = "d-hunk"
        elif raw.startswith("diff ") or raw.startswith("index ") or raw.startswith("new file") or raw.startswith("similarity"):
            cls = "d-meta"
        elif raw.startswith("+"):
            cls = "d-add"
        elif raw.startswith("-"):
            cls = "d-del"
        else:
            cls = "d-ctx"
        out.append(f'<span class="{cls}">{esc or "&nbsp;"}</span>')
    return "\n".join(out)


SECTIONS = [
    {
        "id": "indicator",
        "tag": "ROOT CAUSE",
        "title": "The 134px of empty plate was the &ldquo;Calculating&hellip;&rdquo; indicator",
        "symptom": "At half width the &minus;/+ over Clear block ran 251px wide for 117px of content, with the minus glyph not appearing until 134px in. Three rounds went looking for the width and did not find it: it was fixed on the buttons, then on the track, then the track was pinned.",
        "cause": [
            "<code>globals.css</code> positions <code>.calculating-indicator</code> <code>absolute</code> inside <code>.actions-group</code>, which is <code>position: relative</code>. The floating sheet overrode it to <code>position: static</code> with a grid area of its own",
            "That override was written in the round when <code>.actions-group</code> was <code>display: contents</code> and so had no box to position against. The very next commit restored it as a real box &mdash; and the override was never taken back",
            "So a 134px element that is <code>visibility: hidden</code> almost always sat <strong>in the block&rsquo;s flow</strong>, beside the controls. <code>justify-content: flex-end</code> from globals packed the visible three against the right end, and the hidden one showed as an empty plate on the left",
            "It reached further than the block: the block sat in a <code>max-content</code> track, so its inflated 251px is what stretched the LEGEND directly above it to 251px too &mdash; a second symptom nobody had connected to the first",
        ],
        "fixes": [
            "The indicator goes back out of flow: <code>position: absolute; inset: 0</code>, covering the block rather than standing beside it. The controls it stands in for are <code>.hidden-reserve</code>&rsquo;d by the time it shows, so the two never appear at once",
            "It takes the block&rsquo;s own height instead of <code>--control-height</code>, which is taller than a floating row",
            "Nothing about the buttons changed. They were never wrong: measured 29.4 &times; 29.4 before and after",
        ],
        "files": ["client-react/src/components/TopBar/TopBarFloat.css"],
    },
    {
        "id": "context",
        "tag": "STRUCTURE",
        "title": "The bottom row is its own layout context",
        "symptom": "Two requirements kept trading places: the block sized by its own content, and the Start/End block and legend not moving when it appears. Fixing either broke the other.",
        "cause": [
            "All six controls were items of ONE grid &mdash; <code>.topbar-legend</code> and <code>.topbar-actions</code> are <code>display: contents</code> &mdash; and in a grid a column track is shared by every row",
            "So the block could not be removed without recomputing the tracks the row above sits in. Pinning the tracks (<code>visibility: hidden</code> instead of <code>display: none</code>) fixed the top row and cemented the over-wide block",
            "Neither requirement is a property of the other. They only fought because they were being answered in the same set of tracks",
        ],
        "fixes": [
            "<code>.picker-row</code> wraps Map, Vote and the action block as ONE grid item spanning every column, flexed inside. Its three widths are settled where nothing above can see them",
            "It cannot reach back out either: it spans flexible tracks, so intrinsic track sizing ignores it, and <code>min-width: 0</code> leaves it nothing to push with",
            "The wrapper is BOXLESS (<code>display: contents</code>) at every other width, so the banner&rsquo;s 2&times;2 grid and the floating desktop grid go on placing the three controls themselves &mdash; which is why full and quarter width come out identical to the digit",
            "The rail moved to the front of <code>.topbar-actions</code> to leave three adjacent siblings to wrap, so its cell is now stated explicitly rather than left to auto-placement",
        ],
        "files": ["client-react/src/components/TopBar/TopBar.tsx", "client-react/src/components/TopBar/TopBar.css"],
    },
    {
        "id": "onerule",
        "tag": "ONE RULE",
        "title": "The pills run to the left edge of the block, and take everything when there is none",
        "symptom": "Before a selection the two dropdowns stopped two thirds of the way across, leaving a 260px hole where the block would have been. After one they stopped at the same place, with the block floating far to their right.",
        "cause": [
            "The empty state reserved the block&rsquo;s track on purpose &mdash; that was the only way to stop it dragging the rows above &mdash; so the hole was structural, and no width could close it",
        ],
        "fixes": [
            "With the row independent the empty state can DROP the block outright (<code>display: none</code>), and its flex gap goes with it. That is the whole difference between a hole and no hole",
            "The two pills are <code>flex: 1 1 0</code>, which divides the row&rsquo;s free space rather than its content&rsquo;s leftovers &mdash; so they come out EQUAL whatever is written in them, in both states",
            "One rule covers both states rather than two rules that can drift: the pills run to the left edge of the block, evaluated where there is no block",
        ],
        "files": ["client-react/src/components/TopBar/TopBarFloat.css"],
    },
    {
        "id": "highlight",
        "tag": "STATE",
        "title": "The Start/End highlight says what the row HOLDS",
        "symptom": "With nothing selected, the Start (or End) row was sometimes still tinted its waypoint colour.",
        "cause": [
            "It IS a flag &mdash; <code>activeTool</code>, a <code>useState</code> in RouteContext written from seven call sites &mdash; and the highlight read it directly",
            "But the asymmetry is not the one we were hunting: nothing failed to clear. <code>activeTool</code> RESTS at <code>&quot;start&quot;</code>, so &ldquo;nothing is selected&rdquo; and &ldquo;the Start tool is armed&rdquo; are the same state",
            "That lights the row on arrival, after <code>clearPoints</code> (which resets the flag to its lit value), after deleting the last waypoint (same reset), and &mdash; on the End row &mdash; after clicking End over an empty map. Which row is lit depends on what you last touched, which is what makes it read as intermittent",
        ],
        "fixes": [
            "A row is lit <strong>if and only if it holds a point</strong>, derived from <code>start.coords</code> / <code>end.coords</code> on every render. No path can remove a point without this recomputing, because it IS the point",
            "Which tool the next map click feeds is still tracked and still true; it is just not what this colour is for, and the row&rsquo;s own text (&ldquo;click map to set start&rdquo;) was already saying it better",
            "The <code>!isPointOnly</code> guard went with it &mdash; it existed only to suppress a permanently-on highlight, and was unreachable anyway: point-only maps do not render this legend",
            "<code>aria-pressed</code> still reports the armed tool, which is what a toggle button&rsquo;s pressed state means",
        ],
        "files": ["client-react/src/components/TopBar/TopBar.tsx"],
    },
    {
        "id": "dropdowns",
        "tag": "FUNCTIONAL",
        "title": "Both dropdowns driven for real, not eyeballed",
        "symptom": "A functional break was reported alongside the layout work: the vote-type panel could not be scrolled or clicked. Leaflet eats clicks and wheel on elements above it unless propagation is disabled, and the panel had been re-anchored twice.",
        "cause": [
            "Scrolling was addressed separately in <code>bf4ad8f</code>; this round is the confirmation that <strong>clicking to select</strong> works, and the check that the map picker was not broken by the same cause",
        ],
        "fixes": [
            "Verified by driving it: the panel scrolls (585px of content in a 459px box, <code>overflow-y: auto</code>), <code>elementFromPoint</code> over a row below the fold lands INSIDE the panel (nothing transparent on top, <code>pointer-events: auto</code>), clicking it changes the vote type and closes the panel, and the map picker navigates",
            "All four widths, both controls. The re-parenting in this change (the rail moving to the front of <code>.topbar-actions</code>) is exactly the class of edit that could have broken it, which is why it was re-driven after",
        ],
        "files": ["client-react/src/components/TopBar/TopBar.tsx"],
    },
]


MEASURE = [
    ("1280", "full", "block 74 = two squares + edges", "330 / 135", "330 / 135", "identical to before this change"),
    ("640", "half", "block 251.8 for 116.8 &rarr; <strong>118.8</strong>", "364.2 / 251.8", "502.6 / 113.4", "the width being fixed"),
    ("427", "third", "block 62.8 = two squares + edges", "228 / 175", "228 / 175", "identical to before this change"),
    ("327", "quarter", "block 62.8 = two squares + edges", "143 / 160", "143 / 160", "identical to before this change"),
]


VERIFY = [
    "<strong>Measured, not eyeballed</strong>, with a headless Chrome driving the live dev app at each width in both states, reading <code>getBoundingClientRect</code> off the real boxes.",
    "At half width the block is <strong>118.8px for 116.8px of children</strong> &mdash; the 2px is its own border &mdash; and the squares measure <strong>29.4 &times; 29.4</strong>. Before: 251.8 for the same 116.8.",
    "Empty state at half width: pills <strong>308 / 308</strong>, both ending at <strong>632</strong> &mdash; the same right edge as the legend and the icon rail. Before, they stopped at 372.2 and never moved.",
    "Selected state at half width: pills <strong>244.6 / 244.6</strong>, block <strong>513.2&ndash;632</strong>, gap <strong>8.0</strong> &mdash; one <code>--float-gap</code>.",
    "<strong>Toggling is the test</strong>: Start/End 502.6 and legend 113.4 in BOTH states, at every width. The top row cannot move now for a structural reason rather than a measured one &mdash; it no longer shares a track with anything in the row below.",
    "<strong>Full and quarter width are identical to before, digit for digit</strong>, confirmed by re-running the same probe against the pre-change files and diffing the numbers. Third width likewise.",
    "Band edges checked at <strong>579 / 580</strong> and <strong>1080 / 1081</strong>: each arrangement holds cleanly to its own side of the breakpoint.",
    "Zero border-radius holds everywhere &mdash; the one new box (<code>.picker-row</code>) is boxless outside the band and carries no background, border or radius inside it; one shadow per floating object is unchanged, since no element gained or lost a plate.",
    "The highlight invariant is checked against React&rsquo;s OWN answer rather than the test&rsquo;s: <code>.legend-coords.empty</code> is the app&rsquo;s rendering of &ldquo;this row is empty&rdquo;, so each assertion is <code>lit === holds</code>. Eight states &times; four widths = <strong>32 checks, all passing</strong>: fresh load, start+end, vote type switched mid-selection, vote cast, Clear, start only, Clear again, and End armed over an empty map.",
    "<code>npx tsc -b</code> clean (Vite dev never type-checks) and the full client suite green: <strong>688 passed, 1 skipped</strong>.",
    "<strong>Not deployed.</strong> Local dev only.",
]

CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-tactical</code> at half width with nothing selected: the two dropdowns should run the full width of the band, ending on the same right edge as the legend above them.",
    "Click the map to place a start. The &minus;/+ and Clear should appear as one short block on the right, the two squares SQUARE, and the dropdowns should stop one gap short of it &mdash; and the Start/End block and the legend above should not move by a pixel.",
    "Press Clear and confirm the block leaves and the dropdowns retake the full width, again without the row above moving.",
    "Check the other three widths (full, third, quarter) look exactly as you last approved them &mdash; the numbers say they are untouched, but they are the ones worth a glance.",
    "Open the Vote dropdown, scroll to something below the fold, and CLICK it: the type should change and the panel close. Same for the Map picker.",
    "Cycle the highlight: place a start (row lights), press Clear (goes dark), place a start and cast a vote (stays lit, the selection survives), switch vote type mid-selection (stays lit). Then click End over an empty map &mdash; it should stay dark, which is where the old behaviour lit it.",
]


def li(items):
    return "\n".join(f"<li>{x}</li>" for x in items)


def section_html(s):
    return f"""
    <section class="card" id="{s['id']}">
      <div class="tag">{s['tag']}</div>
      <h2>{s['title']}</h2>
      <h3>Symptom</h3>
      <p>{s['symptom']}</p>
      <h3>Root cause</h3>
      <ul>{li(s['cause'])}</ul>
      <h3>What changed</h3>
      <ul>{li(s['fixes'])}</ul>
      <h3>Files touched</h3>
      <ul class="files">{li(html.escape(f) for f in s['files'])}</ul>
    </section>
    """


# ── Hierarchical "where does this block sit" context ──────────────────────────
# For every diff we render a recursively-summarized map: the SYSTEM (which of the
# app's components this file belongs to) → the MODULE (subsystem + one-line
# summary) → the FILE (summary + an outline of its top-level sections) → the
# changed BLOCKS. The file outline is a focus+context minimap: changed sections
# are highlighted, the rest are dimmed, so you see where the diff lands among its
# peers at every zoom level.

SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

# label, summary, changed?
FILE_CONTEXT = {
    "client-react/src/components/TopBar/TopBar.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 TopBar", "The map's top chrome: the Start/End route legend, the Map and Vote pickers, the meta rail, and the cast controls. One component, two treatments \u2014 floating plates over the map, or the original opaque banner."),
        "file": ("TopBar.tsx", "~340 LOC \u2014 route context wiring, derived class names, the legend markup, the actions markup"),
        "outline": [
            ("imports / useRoute destructure", "unchanged", False),
            ("isPointOnly / isLoading / formatLocation", "unchanged \u2014 station networks vote on one point, so the legend is dropped whole", False),
            ("handleStartClick / handleEndClick / handleAddressSelect", "unchanged \u2014 these are what arm a tool", False),
            ("canVote / placeholders", "unchanged", False),
            ("startToolClass / endToolClass", "the highlight now derives from start.coords / end.coords instead of activeTool", True),
            ("legend markup (.topbar-legend)", "unchanged \u2014 the two stacked pairs keep their placement", False),
            (".topbar-actions \u2192 NavRail", "moved to the FIRST child, so three adjacent siblings remain to wrap", True),
            (".topbar-actions \u2192 .picker-row (new)", "wraps Map, Vote and the action block as one element", True),
            ("cast group / Clear", "unchanged, re-indented one level", False),
            ("HowItWorksModal", "unchanged", False),
        ],
        "blocks": [
            "The wrapper encloses ADJACENT SIBLINGS only, which is the whole reason the rail had to move: it sat between Map and Vote, and it belongs to a different row",
            "Moving the rail is layout-neutral by construction \u2014 every arrangement places it by an explicit grid-area, and the one that did not (the banner) gets an explicit cell in TopBar.css",
            "startToolClass: `if (start.coords)` replaces `if (!isPointOnly && activeTool === \"start\")`",
            "The !isPointOnly guard was unreachable: the whole .topbar-legend block is already gated on !isPointOnly two levels up",
            "aria-pressed still reads activeTool \u2014 the button's toggle state is a different question from what the row holds",
            "data-coach attributes (start / end / votetype / cast) are untouched; the onboarding coach measures from wherever they land",
        ],
    },
    "client-react/src/components/TopBar/TopBar.css": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 TopBar", "The chrome-agnostic half of the bar's styling \u2014 what is true in BOTH the floating and the banner treatments. Everything scoped to [data-chrome=\"float\"] lives in TopBarFloat.css instead."),
        "file": ("TopBar.css", "~100 lines \u2014 the wordmark, the mobile wordmark, the rail's cell, the loading pulse"),
        "outline": [
            (".logo-container / .logo-img", "unchanged", False),
            ("[data-basemap=light] inversion", "unchanged", False),
            (".logo-mobile-banner + @media (max-width: 1080px)", "unchanged", False),
            (".picker-row", "new \u2014 display: contents, the boxless default", True),
            (".topbar-actions > .nav-rail", "gains an explicit grid-area: 1 / 2", True),
            (".route-field-value.loading / @keyframes pulse", "unchanged", False),
        ],
        "blocks": [
            "display: contents is what makes the new wrapper free: its children take part in the PARENT's layout, in document order, so every existing placement keeps working untouched",
            "That is also what protects the banner treatment, whose .topbar-actions is a 2x2 grid filled by auto-placement",
            "grid-area: 1 / 2 reproduces exactly where flow placement had put the rail, now that it is the first child rather than the second",
            "It is stated here rather than in the float sheet because the BANNER is the treatment that needs it \u2014 the floating chrome already overrides the rail's placement at both of its widths",
        ],
    },
    "client-react/src/components/TopBar/TopBarFloat.css": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 TopBar \u00b7 floating chrome", "The whole floating treatment: plates on the map instead of an opaque lid. Three arrangements of the same six controls \u2014 desktop (>1080), a side-by-side band (580\u20131080), and a stacked one (<580)."),
        "file": ("TopBarFloat.css", "~1520 lines \u2014 tokens, the desktop grid, the pairs, the pickers, the two responsive arrangements, overlay anchoring"),
        "outline": [
            ("tokens (:root of .topbar[data-chrome=float])", "unchanged \u2014 --float-gap, --float-row, --pill-cap, radius held at 0", False),
            ("plate / shadow rules", "unchanged \u2014 one shadow per floating object", False),
            ("desktop grid (.topbar-content, 6 columns)", "unchanged", False),
            (".actions-group base box", "unchanged \u2014 the block is a real box with its own 2 rows", False),
            (".calculating-indicator", "back out of flow: absolute, inset 0, the block's own height", True),
            ("legend pairs / pickers / dropdown panels", "unchanged", False),
            ("@media (max-width: 1080px) stacked arrangement", "unchanged \u2014 quarter width", False),
            ("has-selection span rules", "unchanged \u2014 they belong to the stacked arrangement", False),
            ("@media (580\u20131080px) the band", "the bottom row becomes .picker-row, flexed; the block leaves the row when empty", True),
            ("overlay anchoring (.mode-dropdown / .vote-type-dropdown)", "unchanged", False),
        ],
        "blocks": [
            ".calculating-indicator: position static + grid-area 1/4/3/5 \u2192 absolute + inset 0; height auto so it takes the block's row rather than --control-height",
            ".picker-row: display flex, grid-area 5/1/6/4, min-width 0, gap --float-gap \u2014 one item spanning every column",
            "min-width: 0 plus the flexible tracks it spans is what keeps it from writing back into the grid it sits in",
            ".mode-switcher-group / .vote-switcher-group: flex 1 1 0, so the pills divide FREE SPACE and come out equal, not their content's leftovers",
            ".actions-group: flex 0 0 auto + width max-content; justify-self and grid-area dropped \u2014 meaningless for a flex item",
            "The block is pinned right by being LAST in a flex line whose other two take everything else, rather than by being pushed there",
            ":not(.has-selection) .actions-group \u2192 display: none, replacing the grid-column: auto reset. Only safe because the row no longer shares tracks with the rows above",
            "Three stale comments corrected: track 3 is the legend's width now, not the block's",
        ],
    },
}


def context_html(path: str) -> str:
    ctx = FILE_CONTEXT.get(path)
    if not ctx:
        return ""
    pills = "".join(
        f'<span class="pill {"on" if c in ctx["on"] else ""}">{html.escape(c)}</span>'
        for c in SYSTEM_COMPONENTS
    )
    mod_label, mod_sum = ctx["module"]
    file_label, file_sum = ctx["file"]
    outline = "".join(
        f'<li class="{"changed" if changed else "dim"}">'
        f'<span class="ol-label">{html.escape(label)}</span>'
        f'<span class="ol-sum">{html.escape(summary)}</span>'
        f'{"<span class=\"ol-tag\">changed</span>" if changed else ""}</li>'
        for (label, summary, changed) in ctx["outline"]
    )
    blocks = "".join(f"<li>{html.escape(b)}</li>" for b in ctx["blocks"])
    return f"""
    <div class="ctx">
      <div class="ctx-tier ctx-sys">
        <span class="ctx-k">System</span>
        <span class="ctx-v">{SYSTEM_NAME}</span>
        <span class="pills">{pills}</span>
      </div>
      <div class="ctx-tier ctx-mod">
        <span class="ctx-k">Module</span>
        <span class="ctx-v">{html.escape(mod_label)}</span>
        <span class="ctx-sum">{html.escape(mod_sum)}</span>
      </div>
      <div class="ctx-tier ctx-file">
        <span class="ctx-k">File</span>
        <span class="ctx-v">{html.escape(file_label)}</span>
        <span class="ctx-sum">{html.escape(file_sum)}</span>
      </div>
      <div class="ctx-map">
        <div class="ctx-map-title">File map — where this diff sits <span class="legend"><span class="sw-ch"></span>changed&nbsp;&nbsp;<span class="sw-dim"></span>context</span></div>
        <ul class="ctx-outline">{outline}</ul>
      </div>
      <div class="ctx-map">
        <div class="ctx-map-title">Changed blocks (top → bottom)</div>
        <ol class="ctx-blocks">{blocks}</ol>
      </div>
    </div>
    """


def measure_table() -> str:
    rows = "".join(
        f"<tr><td><code>{w}px</code></td><td>{name}</td><td>{block}</td>"
        f"<td class=\"num\">{before}</td><td class=\"num\">{after}</td><td class=\"note\">{note}</td></tr>"
        for (w, name, block, before, after, note) in MEASURE
    )
    return f"""
    <div class="card">
      <div class="tag">Measured</div>
      <h2>Four widths, both states</h2>
      <table class="measure">
        <thead><tr><th>viewport</th><th></th><th>&minus;/+ over Clear block</th>
          <th>Start/End &middot; legend<br><span class="th-sub">before</span></th>
          <th>Start/End &middot; legend<br><span class="th-sub">after</span></th><th></th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="font-size:13px;color:var(--muted);margin-top:12px;">Start/End and legend widths are identical in the
      empty and selected states at every width &mdash; that is the toggle test, and it is the column that used to fail.
      Half width&rsquo;s legend is 113.4 rather than 251.8 because it was only ever that wide by inheriting the block&rsquo;s
      inflated track; it now measures its own &ldquo;Selection&rdquo; and &ldquo;Votes&rdquo;.</p>
    </div>
    """


def shots() -> str:
    import base64
    if not SHOT_DIR:
        return ""
    pairs = [
        ("Before &mdash; nothing selected", "before/w640-empty.png",
         "After &mdash; nothing selected", "after/w640-empty.png"),
        ("Before &mdash; start selected", "before/w640-selected.png",
         "After &mdash; start selected", "after/w640-selected.png"),
    ]
    out = []
    for (la, fa, lb, fb) in pairs:
        cells = []
        for (label, fn) in ((la, fa), (lb, fb)):
            path = os.path.join(SHOT_DIR, fn)
            if not os.path.exists(path):
                continue
            with open(path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
            cells.append(f'<figure><figcaption>{label}</figcaption>'
                         f'<img alt="{label}" src="data:image/png;base64,{b64}"></figure>')
        out.append(f'<div class="shots">{"".join(cells)}</div>')
    return f"""
    <div class="card">
      <div class="tag">Half width</div>
      <h2>What it looked like</h2>
      {"".join(out)}
    </div>
    """


def main():
    with open(DIFF_PATH) as f:
        diff_text = f.read()
    files = split_by_file(diff_text)

    diff_blocks = []
    for name, chunk in files:
        added = sum(1 for ln in chunk.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in chunk.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        diff_blocks.append(f"""
        <details>
          <summary><span class="fname">{html.escape(name)}</span>
            <span class="stat"><span class="d-add">+{added}</span> / <span class="d-del">-{removed}</span></span>
          </summary>
          {context_html(name)}
          <pre class="diff">{colorize(chunk)}</pre>
        </details>
        """)

    sections_html = "\n".join(section_html(s) for s in SECTIONS)
    measure_html = measure_table()
    shots_html = shots()
    nav = "\n".join(f'<a href="#{s["id"]}">{s["title"].split("·")[0].strip()}</a>' for s in SECTIONS)

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITLE} — {DATE}</title>
<style>
  :root {{
    --paper: #fbfbf8; --ink: #15140f; --muted: #6b6760; --hairline: rgba(0,0,0,0.10);
    --accent: #b4541e; --add-bg: #e6ffec; --add-fg: #06402b; --del-bg: #ffeef0; --del-fg: #82071e;
    --code-bg: #f1efe9; --font-ui: "Source Sans 3", system-ui, -apple-system, sans-serif;
    --font-ed: "Source Serif 4", Georgia, serif; --font-mono: ui-monospace, "SF Mono", Menlo, monospace;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; background: var(--paper); color: var(--ink); font-family: var(--font-ui); line-height: 1.6; }}
  .wrap {{ max-width: 920px; margin: 0 auto; padding: 48px 24px 96px; }}
  header.masthead {{ border-bottom: 2px solid var(--ink); padding-bottom: 20px; margin-bottom: 8px; }}
  .kicker {{ font-size: 13px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--accent); font-weight: 600; }}
  h1 {{ font-family: var(--font-ed); font-size: 34px; line-height: 1.15; margin: 8px 0 6px; }}
  .dateline {{ color: var(--muted); font-size: 14px; }}
  nav.toc {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 24px 0 8px; }}
  nav.toc a {{ font-size: 13px; text-decoration: none; color: var(--ink); border: 1px solid var(--hairline);
    border-radius: 999px; padding: 5px 12px; background: #fff; }}
  nav.toc a:hover {{ border-color: var(--accent); color: var(--accent); }}
  .lede {{ font-family: var(--font-ed); font-size: 18px; color: #2c2a24; margin: 20px 0 28px; }}
  .card {{ background: #fff; border: 1px solid var(--hairline); border-radius: 14px; padding: 24px 26px;
    margin: 20px 0; box-shadow: 0 1px 0 rgba(0,0,0,0.02); }}
  .tag {{ display: inline-block; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--muted); border: 1px solid var(--hairline); border-radius: 6px; padding: 2px 8px; margin-bottom: 10px; }}
  h2 {{ font-family: var(--font-ed); font-size: 24px; margin: 4px 0 14px; }}
  h3 {{ font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent);
    margin: 20px 0 6px; }}
  ul {{ margin: 6px 0 0; padding-left: 22px; }}
  li {{ margin: 6px 0; }}
  ul.files li {{ font-family: var(--font-mono); font-size: 12.5px; color: #33312b; }}
  code {{ font-family: var(--font-mono); font-size: 0.88em; background: var(--code-bg);
    padding: 1px 5px; border-radius: 4px; }}
  p {{ margin: 6px 0; }}
  .twocol {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  @media (max-width: 680px) {{ .twocol {{ grid-template-columns: 1fr; }} h1 {{ font-size: 27px; }} }}
  details {{ border: 1px solid var(--hairline); border-radius: 10px; margin: 10px 0; background: #fff; overflow: hidden; }}
  summary {{ cursor: pointer; padding: 10px 14px; font-family: var(--font-mono); font-size: 13px;
    display: flex; justify-content: space-between; align-items: center; gap: 12px; user-select: none; }}
  summary:hover {{ background: #faf8f3; }}
  .fname {{ color: var(--ink); }} .stat {{ font-size: 12px; color: var(--muted); }}
  pre.diff {{ margin: 0; padding: 14px 16px; overflow-x: auto; background: #fcfbf7;
    border-top: 1px solid var(--hairline); font-family: var(--font-mono); font-size: 12px; line-height: 1.5; }}
  pre.diff span {{ display: block; white-space: pre; }}

  /* Hierarchical context map (System ▸ Module ▸ File ▸ blocks) */
  .ctx {{ padding: 14px 16px; background: #f7f5ef; border-top: 1px solid var(--hairline); }}
  .ctx-tier {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; padding: 4px 0; }}
  .ctx-mod {{ padding-left: 18px; }} .ctx-file {{ padding-left: 36px; }}
  .ctx-tier {{ position: relative; }}
  .ctx-mod::before, .ctx-file::before {{ content: "└"; position: absolute; left: 4px; color: #bdb8ac; }}
  .ctx-file::before {{ left: 22px; }}
  .ctx-k {{ font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: #fff;
    background: var(--accent); border-radius: 4px; padding: 2px 6px; }}
  .ctx-mod .ctx-k, .ctx-file .ctx-k {{ background: #8a857a; }}
  .ctx-v {{ font-family: var(--font-mono); font-size: 13px; font-weight: 600; color: var(--ink); }}
  .ctx-sum {{ font-size: 12.5px; color: var(--muted); }}
  .pills {{ display: inline-flex; flex-wrap: wrap; gap: 5px; }}
  .pill {{ font-size: 11px; color: #8a857a; background: #fff; border: 1px solid var(--hairline);
    border-radius: 999px; padding: 1px 9px; }}
  .pill.on {{ color: #fff; background: var(--accent); border-color: var(--accent); font-weight: 600; }}
  .ctx-map {{ margin-top: 12px; padding-left: 36px; }}
  .ctx-map-title {{ font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted);
    margin-bottom: 6px; display: flex; gap: 12px; align-items: center; }}
  .legend {{ font-size: 11px; text-transform: none; letter-spacing: 0; color: var(--muted); display: inline-flex; align-items: center; }}
  .sw-ch, .sw-dim {{ display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }}
  .sw-ch {{ background: var(--accent); }} .sw-dim {{ background: #d6d1c5; }}
  ul.ctx-outline {{ list-style: none; margin: 0; padding: 0; border-left: 2px solid #e3ded2; }}
  ul.ctx-outline li {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px;
    margin: 0; padding: 4px 0 4px 12px; position: relative; }}
  ul.ctx-outline li.changed {{ border-left: 3px solid var(--accent); margin-left: -2px; padding-left: 11px; background: #fff6ef; }}
  ul.ctx-outline li.dim {{ opacity: 0.62; }}
  .ol-label {{ font-family: var(--font-mono); font-size: 12.5px; color: var(--ink); }}
  li.changed .ol-label {{ font-weight: 700; color: var(--accent); }}
  .ol-sum {{ font-size: 12px; color: var(--muted); }}
  .ol-tag {{ font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase; color: #fff;
    background: var(--accent); border-radius: 4px; padding: 1px 6px; }}
  ol.ctx-blocks {{ margin: 0; padding-left: 20px; }}
  ol.ctx-blocks li {{ font-family: var(--font-mono); font-size: 12px; color: #33312b; margin: 4px 0; }}

  table.measure {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }}
  table.measure th {{ text-align: left; font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--hairline); padding: 6px 10px 6px 0; vertical-align: bottom; }}
  table.measure .th-sub {{ text-transform: none; letter-spacing: 0; font-weight: 400; color: var(--accent); }}
  table.measure td {{ border-bottom: 1px solid var(--hairline); padding: 8px 10px 8px 0; vertical-align: top; }}
  table.measure td.num {{ font-family: var(--font-mono); font-size: 12px; }}
  table.measure td.note {{ color: var(--muted); font-size: 12px; }}
  .shots {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin: 14px 0; }}
  @media (max-width: 680px) {{ .shots {{ grid-template-columns: 1fr; }} }}
  .shots figure {{ margin: 0; }}
  .shots figcaption {{ font-size: 12px; color: var(--muted); margin-bottom: 6px; }}
  .shots img {{ width: 100%; display: block; border: 1px solid var(--hairline); border-radius: 8px; }}

  .d-add {{ background: var(--add-bg); color: var(--add-fg); }}
  .d-del {{ background: var(--del-bg); color: var(--del-fg); }}
  .d-hunk {{ color: #5a3ec8; }} .d-meta {{ color: var(--muted); }} .d-ctx {{ color: #33312b; }}
  .checklist li, .verify li {{ margin: 8px 0; }}
  footer {{ margin-top: 40px; color: var(--muted); font-size: 13px; border-top: 1px solid var(--hairline); padding-top: 16px; }}
</style>
</head>
<body>
<div class="wrap">
  <header class="masthead">
    <div class="kicker">City Edit · Change log</div>
    <h1>{TITLE}</h1>
    <div class="dateline">{DATE} · branch <code>main</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">The &minus;/+ over Clear block at half width was never wide. A hidden &ldquo;Calculating&hellip;&rdquo; indicator was standing beside it &mdash; left in the block&rsquo;s flow by an override written for a structure that lasted one commit &mdash; and its 134px is both the empty plate before the minus and, through the shared column track, the reason the legend above was stretched to match. With that gone, the bottom row is given a layout context of its own, which lets the block be sized by its content AND the rows above stay put: the two requirements that had been trading places for three rounds. Plus the bar&rsquo;s Start/End highlight, which was reading a mode flag whose resting value was the lit one, now derives from the point itself.</p>

  {shots_html}
  {measure_html}

  {sections_html}

  <section class="card verify" id="verify">
    <div class="tag">Verification</div>
    <h2>What was run</h2>
    <ul>{li(VERIFY)}</ul>
  </section>

  <section class="card checklist" id="checklist">
    <div class="tag">For you</div>
    <h2>Manual checklist</h2>
    <ul>{li(CHECKLIST)}</ul>
  </section>

  <section id="diff">
    <h2 style="font-family:var(--font-ed);font-size:24px;margin:32px 0 10px;">Full diff</h2>
    <p style="color:var(--muted);font-size:14px;margin-top:0;">Click any file to expand. Green is added, red removed.</p>
    {''.join(diff_blocks)}
  </section>

  <footer>
    Generated from <code>changelog/changes-halfwidth.diff</code> by <code>changelog/build_halfwidth.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes-halfwidth.diff &amp;&amp; python changelog/build_halfwidth.py</code>.
  </footer>
</div>
</body>
</html>
"""
    with open(OUT_PATH, "w") as f:
        f.write(doc)
    print(f"Wrote {OUT_PATH} ({len(files)} files in diff)")


if __name__ == "__main__":
    main()
