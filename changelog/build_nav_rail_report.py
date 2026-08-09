#!/usr/bin/env python3
"""Generate the HTML changelog report from the captured unified diff.

Run from repo root: python changelog/build_report.py
Reads changelog/changes.diff, writes changelog/2026-08-09-nav-rail-blog-link.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-nav-rail.diff")
OUT_PATH = os.path.join(HERE, "2026-08-09-nav-rail-blog-link.html")

DATE = "2026-08-09"
TITLE = "A blog link, and a header with room for it"


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
        "id": "clutter",
        "tag": "THE PROBLEM",
        "title": "Five links wanted the same row",
        "symptom": "The map header carried Feedback, About and Donate as full-width text buttons, with How it Works sitting a row below among the vote controls. The blog had to go somewhere, and a fourth label in that row was not an option \u2014 the meta links were already as loud as the controls people come to the map to use.",
        "cause": [
            "Every one of them was a <code>.btn-header</code>: same size, same weight, same border as each other AND as the Clear button next to the vote controls. Nothing in the bar said which things were the map and which were the website",
            "How it Works was in <code>.actions-group</code> \u2014 the row-2 cell that otherwise acts on the current selection \u2014 purely to stop that row collapsing to zero height before a selection existed",
            "Together they set the header's right-hand column at <strong>395px</strong>, which the legend then had to fit alongside",
        ],
        "fixes": [
            "One shared <code>NavRail</code>: two enclosed segments of icon buttons \u2014 <strong>How it Works \u00b7 Blog \u00b7 About</strong> | <strong>Feedback \u00b7 Donate</strong>",
            "Grouping is carried by <em>enclosure</em>, not by a divider or a heading: the first box is things to read, the second is things to do. How it Works belongs in the first, so it moves out of the selection row",
            "That row now legitimately stays empty until there is a selection \u2014 the fixed <code>grid-template-rows</code> was always what held it open, not the button",
            "Right-hand column: <strong>395px \u2192 294px</strong>",
        ],
        "files": ["client-react/src/components/NavRail/NavRail.tsx", "client-react/src/components/TopBar/TopBar.tsx"],
    },
    {
        "id": "icons",
        "tag": "THE ICON SET",
        "title": "Drawn to the app's grid, not imported",
        "symptom": "Icon-only buttons need icons, and the obvious move \u2014 pull in Lucide or Feather \u2014 would have put rounded caps and 2px strokes on a 24px grid next to a monospace, zero-radius, hairline interface.",
        "cause": [
            "The app already has a drawing language: <code>CheckIcon</code> is explicitly square-capped and mitered \u201cunlike the rounded unicode \u2713\u201d, and the logo is letters set in a crossword grid of squares",
            "A pack's icons are internally consistent with each other, which is exactly what makes them read as a foreign body \u2014 they are consistent with the wrong thing",
        ],
        "fixes": [
            "Five glyphs on one skeleton: a 24 grid, 1.75px strokes, <code>square</code> caps, <code>miter</code> joins, and every mark built from a rectangle on that grid",
            "<code>?</code> and <code>i</code> are set in the logo's lettered box, so the two informational marks read as a pair; the article and the speech bubble are the same rectangle carrying different contents",
            "The heart is the one curve in the set \u2014 which is what makes Donate the glyph the eye lands on first",
            "The article's image block is <em>filled</em> rather than stroked: at the 15\u201318px these render at, a 6\u00d75 stroked box is all outline and silts up into a grey smudge",
        ],
        "files": ["client-react/src/components/NavRail/icons.tsx"],
    },
    {
        "id": "hierarchy",
        "tag": "HIERARCHY",
        "title": "Sizing by how much each thing matters",
        "symptom": "Decluttering by shrinking everything equally just makes a smaller undifferentiated row. The bar needed to say what it is for.",
        "cause": [
            "The controls that do the work \u2014 the Map and Vote selectors, the Cast \u2212/+ buttons \u2014 were the same visual weight as a link to an external website",
            "Donate is the one meta item with something to ask for, and it was one of three identical siblings",
        ],
        "fixes": [
            "The working controls are <strong>untouched</strong>: full <code>--control-height</code>, full labels, and Cast keeps its 44px buttons",
            "Everything in the rail goes icon-only \u2014 <em>except</em> Donate, which keeps its word and the active map's accent fill, so one item in the quiet half still reads first",
            "Below 1080px Donate sheds its label too and joins the grid of squares; the width that frees goes to the Map/Vote selectors, which is where it is worth spending",
            "Clear deliberately keeps its text: a bare <code>\u00d7</code> sitting beside <code>\u2212</code> and <code>+</code> reads as an operator",
            "Tooltips are hover-only (<code>@media (hover: hover)</code>) so a tap never leaves one stuck open; <code>aria-label</code> carries the name regardless",
        ],
        "files": ["client-react/src/components/NavRail/NavRail.css"],
    },
    {
        "id": "overflow",
        "tag": "BUG FOUND EN ROUTE",
        "title": "The desktop header had a hard 1353px floor",
        "symptom": "Measuring the new rail at 1300px showed the topbar overflowing its viewport by 53px, with Donate clipped at the edge. It was not the rail: the same measurement against the pre-change markup put the floor at ~1454px.",
        "cause": [
            "<code>.topbar-legend</code> was <code>flex-shrink: 0</code>, and it holds the widest thing in the bar \u2014 a 250px address slot",
            "So the desktop layout could never be narrower than legend + selectors + links. Every window between the 1080px breakpoint and ~1353px scrolled sideways",
            "The truncation machinery already existed and was already commented at length \u2014 it was just scoped to the \u22641080 block, so above the breakpoint the address could never ellipsise",
        ],
        "fixes": [
            "<code>flex-shrink: 0</code> \u2192 <code>min-width: 0</code> on the legend, and <code>.legend-item-coords { min-width: 0 }</code> promoted out of the \u22641080 block so it holds at every width",
            "The address now ellipsises above 1080 exactly as it already did below it",
            "The rail is pinned with <code>justify-self: end</code> so its right edge stays flush with the legend's whichever of the rail / Cast+Clear happens to be wider \u2014 they currently tie almost exactly, and relying on that tie would be luck",
        ],
        "files": ["client-react/src/styles/globals.css", "client-react/src/components/TopBar/TopBar.css"],
    },
    {
        "id": "landing",
        "tag": "LANDING PAGE",
        "title": "One nav instead of two",
        "symptom": "The landing page had About / Feedback / Donate as footer links. Adding Blog there would have made four links at the bottom of a page whose entire job is picking a map.",
        "cause": [
            "A footer is where links go when there is nowhere better, and on this page there was somewhere better \u2014 the header is mostly empty space either side of a centred logo",
        ],
        "fixes": [
            "The same rail, pinned top-right of the header; the footer drops to the copyright line alone",
            "Below 720px it unpins and returns to the column under the tagline \u2014 in the corner it would clip the logo's top-left square at that width",
            "It is <em>last</em> in the DOM for that reason: pinned, position is cosmetic, but in the column source order is the visual order, and so the focus order",
            "No How it Works here \u2014 that glyph describes this map's controls and has nothing to say on the picker, so it is dropped rather than shown inert",
            "Donate keeps its label at every width on this page: the 1080px rule exists because the <em>map</em> header is fighting for room, and this page is not",
        ],
        "files": ["client-react/src/components/Landing/Landing.tsx", "client-react/src/components/Landing/Landing.css"],
    },
]



VERIFY = [
    "Measured, not eyeballed: at <strong>320 / 360 / 390 / 430 / 560 / 768 / 900 / 1024 / 1100 / 1200 / 1300 / 1440px</strong> the document's <code>scrollWidth</code> equals the viewport at every step \u2014 <strong>no horizontal overflow at any width</strong>.",
    "The rail's right edge and the legend's right edge come out <strong>identical at every one of those widths</strong> (e.g. both 370 at 390px, both 1280 at 1300px).",
    "The 101px saving is a measurement, not an estimate: the pre-change markup was rebuilt in the live DOM and measured at <strong>395px</strong> for the old column against <strong>294px</strong> for the rail.",
    "<code>.topbar</code> does still report internal overflow below 768px \u2014 traced to the <em>closed</em> <code>.mode-dropdown</code> panel, which is pre-existing, absolutely positioned, and does not reach the document.",
    "Driven in the running app: hovering a glyph shows its tooltip (<em>How it works</em>, <em>Blog</em>), and clicking the <code>?</code> opens the How it Works modal from its new home in the rail.",
    "Link attributes read back from the DOM \u2014 all four anchors carry <code>target=_blank</code>, <code>rel=\"noopener noreferrer\"</code> and an <code>aria-label</code>, with Blog resolving to <code>https://sphericalharmonics.substack.com/</code>.",
    "Responsive behavior was verified in <strong>iframes</strong> rather than by resizing the window \u2014 the window would not resize, and short iframes silently triggered the <code>(max-height: 500px)</code> landscape query, which collapses the legend and made an early reading look broken. Frames were then sized past 500px tall.",
    "The landing rail was checked at 1000px (pinned top-right) and 390px (in-column under the tagline).",
    "<code>npx tsc -b</code> clean and <code>npm run build</code> succeeds \u2014 Vite dev never type-checks, so this is the only thing between an edit and a broken client build.",
    "Committed as a <strong>partial stage</strong>: another session is mid-flight in <code>TopBar.tsx</code> (the Vote \u2192 Proposals selector change), so only this workstream's hunks were staged and <code>git show :\u2026TopBar.tsx</code> was read back to confirm their edit was neither committed nor lost from the working tree.",
]



CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-walkways</code> \u2014 expect two small boxes of icons at the top right: <strong>[?] [\u25a4] [i]</strong> then <strong>[\u25ad] [\u2661 Donate]</strong>, with Donate in the map's accent.",
    "Hover each glyph and confirm the tooltip names it; click <code>[?]</code> and confirm the How it Works modal still opens.",
    "Click the newspaper glyph and confirm it opens <code>sphericalharmonics.substack.com</code> in a new tab.",
    "Drag the window narrow and back \u2014 watch the Donate label drop away around 1080px and the start/end address ellipsise instead of the bar running off the right edge. <strong>Nothing should scroll sideways at any width.</strong>",
    "Place a start and end and confirm the Cast \u2212/+ and Clear controls appear on the row below, unchanged in size.",
    "Open <code>http://localhost:3000/?landing=1</code> \u2014 rail top-right, and the footer now shows only the copyright line. Narrow past 720px and confirm it moves to under \u201cRedraw your city.\u201d",
    "On a phone, check the icon buttons are comfortable to hit \u2014 they are 32px wide at \u22641080 and 29px at \u2264400, which is the one judgement call here worth a second opinion.",
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
    "client-react/src/components/NavRail/NavRail.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 NavRail", "New shared component: the secondary/meta nav carried by both the map header and the landing header, so these links have one definition instead of two"),
        "file": ("NavRail.tsx", "~95 LOC \u2014 four destination constants and one component of two enclosed groups"),
        "outline": [
            ("BLOG_URL / ABOUT_URL / FEEDBACK_URL / DONATE_URL", "new \u2014 the four destinations, exported", True),
            ("NavRailProps", "new \u2014 optional onHowItWorks, optional className", True),
            ("group 1: How it Works \u00b7 Blog \u00b7 About", "new \u2014 things to read", True),
            ("group 2: Feedback \u00b7 Donate", "new \u2014 things to do", True),
        ],
        "blocks": [
            "onHowItWorks is OPTIONAL: the glyph is about this map's controls, so the landing page omits it rather than rendering it inert",
            "The button is a <button> and the rest are <a> \u2014 one opens a modal, four leave the site; they only look alike",
            "Every icon-only control carries an aria-label AND a data-tip: the first is the accessible name, the second is the hover tooltip",
            "target=_blank + rel=noopener noreferrer on all four \u2014 none of them should navigate the map away from a selection",
            "Donate is the only one with a label element, which is what the CSS keys its wider box off",
        ],
    },
    "client-react/src/components/NavRail/icons.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 NavRail", "The glyph set for the rail \u2014 drawn here rather than pulled from an icon package"),
        "file": ("icons.tsx", "~90 LOC \u2014 one shared `base` prop object and five glyphs"),
        "outline": [
            ("header comment", "new \u2014 records the skeleton the set is drawn to, and why an off-the-shelf pack was rejected", True),
            ("base props", "new \u2014 24 grid, 1.75 stroke, square caps, miter joins, currentColor, aria-hidden", True),
            ("IconQuestion / IconInfo", "new \u2014 ? and i set in the logo's lettered box", True),
            ("IconArticle", "new \u2014 Blog: filled image block + two column rules + one full measure", True),
            ("IconBubble", "new \u2014 Feedback: a square bubble with a mitered tail", True),
            ("IconHeart", "new \u2014 Donate: the one curve in the set", True),
        ],
        "blocks": [
            "square caps + miter joins are the whole point \u2014 they are what match CheckIcon and the crossword logo; rounded is what makes a pack look imported",
            "? and i share an identical 17\u00d717 box so the two info marks read as a deliberate pair, not two unrelated glyphs",
            "The article's image block is filled, not stroked: a stroked 6\u00d75 box at 18px is all outline and reads as a smudge",
            "The heart breaks the rectangle rule on purpose \u2014 it is the accent item and should not blend into its neighbours",
            "aria-hidden throughout: the accessible name lives on the surrounding button, never doubled here",
        ],
    },
    "client-react/src/components/NavRail/NavRail.css": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 NavRail", "The rail's own stylesheet \u2014 sizing, the two-segment enclosure, the tooltip, and the responsive steps"),
        "file": ("NavRail.css", "~170 lines \u2014 layout, Donate's accent variant, tooltips, two breakpoints"),
        "outline": [
            (".nav-rail (--nav-btn-size / --nav-icon-size)", "new \u2014 local knobs so a breakpoint moves both together", True),
            (".nav-group", "new \u2014 the enclosure that carries the semantic grouping", True),
            (".nav-btn + dividers + hover/active/focus", "new \u2014 the square icon button", True),
            (".nav-btn-donate", "new \u2014 accent fill, label, wider box", True),
            ("tooltips under @media (hover: hover)", "new \u2014 instant reveal; last one anchors right", True),
            ("prefers-reduced-motion", "new \u2014 transitions off", True),
            ("\u22641080 / \u2264400 steps", "new \u2014 Donate sheds its label; buttons tighten", True),
        ],
        "blocks": [
            "--nav-btn-size is control-height MINUS the group's 1px border, which is what keeps the buttons exactly square at every breakpoint",
            "Enclosure does the grouping: one border around each set, a gap between them, no divider rule or heading needed",
            "Tooltips are gated on (hover: hover) so a tap on touch never leaves one open with nothing to dismiss it",
            "The last group's last tooltip anchors to its own right edge \u2014 the rail sits hard against the header's edge and it would otherwise overflow",
            "Below 1080 the buttons go slightly narrower than tall on purpose: the width goes back to the Map/Vote selectors",
        ],
    },
    "client-react/src/components/NavRail/index.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 NavRail", "Barrel export, matching every other component folder in the client"),
        "file": ("index.ts", "1 LOC"),
        "outline": [("export { NavRail }", "new", True)],
        "blocks": ["Matches the folder convention so the import reads ../NavRail like its neighbours"],
    },
    "client-react/src/components/TopBar/TopBar.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 TopBar", "The map header: logo, the start/end legend, the Map + Vote selectors, the cast controls, and the meta links"),
        "file": ("TopBar.tsx", "~295 LOC \u2014 this change replaces one grid cell and empties another"),
        "outline": [
            ("imports", "+1 \u2014 NavRail", True),
            ("route/selection state + handlers", "unchanged", False),
            ("logo + mobile banner", "unchanged", False),
            ("topbar-legend (start / selection / end / votes)", "unchanged", False),
            ("mode-switcher-group", "unchanged", False),
            ("header-btn-group (Feedback \u00b7 About \u00b7 Donate)", "REMOVED \u2014 replaced by <NavRail>", True),
            ("vote-switcher-group", "unchanged", False),
            ("actions-group: How it Works button", "REMOVED \u2014 moved into the rail", True),
            ("actions-group: calculating / cast / clear", "unchanged", False),
            ("HowItWorksModal", "unchanged \u2014 still owned here, opened by the rail", True),
        ],
        "blocks": [
            "The 2\u00d72 grid keeps exactly four children \u2014 the landscape query flattens it to `auto auto auto auto`, so the count is load-bearing",
            "The rail is one child, which is why the three links needed a wrapper before and no longer do",
            "How it Works moves cell: it is information, not an action on the selection, and row 2 is the selection's row",
            "The modal state stays in TopBar; the rail only receives the opener, so it has no idea what it is opening",
            "Row 2's right cell is now empty until there is a selection \u2014 grid-template-rows was always what held its height",
        ],
    },
    "client-react/src/components/TopBar/TopBar.css": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 TopBar", "The header's own stylesheet \u2014 logo sizing, the mobile banner, and the placement of the header's grid cells"),
        "file": ("TopBar.css", "~80 lines after this change"),
        "outline": [
            (".logo-container / .logo-img / basemap invert", "unchanged", False),
            (".logo-mobile-banner", "unchanged", False),
            (".header-btn-group", "REMOVED \u2014 the three-link wrapper is gone", True),
            (".btn-donate", "REMOVED \u2014 superseded by .nav-btn-donate", True),
            (".topbar-actions > .nav-rail", "new \u2014 justify-self: end", True),
            ("route-field pulse", "unchanged", False),
        ],
        "blocks": [
            "justify-self: end pins the rail to its grid column's right edge, so its edge lines up with the legend's and with Clear's",
            "Without it the alignment depends on the rail happening to be the wider of the two cells \u2014 which it currently is, by a few px",
            "The old .btn-donate hardcoded #111 for its text; .nav-btn-donate reads --on-accent so it follows the theme",
            "Scoped to .topbar-actions > so the landing page's copy of the rail is unaffected",
        ],
    },
    "client-react/src/components/Landing/Landing.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 Landing", "The map picker at cityedit.org: the logo header, the search field, the card grid and the footer"),
        "file": ("Landing.tsx", "~230 LOC \u2014 this change adds the rail and shortens the footer"),
        "outline": [
            ("imports", "+1 \u2014 NavRail", True),
            ("toCard / fallbackCards / skeletons", "unchanged", False),
            ("placeholder-clipping measurement", "unchanged", False),
            ("landing-header (logo + tagline)", "+ NavRail, placed LAST in the DOM", True),
            ("toolbar / search", "unchanged", False),
            ("card grid", "unchanged", False),
            ("landing-footer", "the three duplicate links removed; copyright stays", True),
        ],
        "blocks": [
            "NavRail is last in the header's DOM on purpose \u2014 pinned it does not matter, in-column it makes source order the visual and focus order",
            "No onHowItWorks passed: the glyph describes a map's controls and there is no map here",
            "The footer loses About/Feedback/Donate rather than gaining Blog \u2014 four links at the bottom of a picker page is the clutter, not the cure",
        ],
    },
    "client-react/src/components/Landing/Landing.css": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 Landing", "The picker's stylesheet \u2014 header, search, card grid, footer, and the 720px single-column step"),
        "file": ("Landing.css", "~360 lines"),
        "outline": [
            (".landing-header", "+ position: relative, so the rail can pin to it", True),
            (".landing-nav", "new \u2014 pinned top-right", True),
            (".landing-nav .nav-btn-donate / -label", "new \u2014 Donate keeps its word at every width here", True),
            (".landing-search / cards / skeleton", "unchanged", False),
            (".landing-footer-link", "REMOVED \u2014 no footer links left to style", True),
            ("\u2264720 block", "+ .landing-nav returns to the column flow", True),
        ],
        "blocks": [
            "Pinned in the corner it stays out of the logo/tagline axis, which is what the header is actually for",
            "Below 720 it unpins: the logo grows to fill the width and the corner would clip its top-left square",
            "The Donate-label override is scoped to .landing-nav (0-2-0) so it outranks the rail's own \u22641080 rule without touching the map header",
            "The 1080 rule exists because the MAP header is short of room; this page never is",
        ],
    },
    "client-react/src/styles/globals.css": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 global styles", "The single stylesheet: design tokens, the themeable palette, the topbar grids, map controls, markers and the proposal cards"),
        "file": ("globals.css", "~2,530 lines \u2014 this change touches the legend's flex behavior and two comments"),
        "outline": [
            ("design tokens + palette", "unchanged \u2014 --control-height / --accent are what the rail reads", False),
            (".topbar / .topbar-content", "unchanged", False),
            (".topbar-legend flex-shrink", "CHANGED \u2014 flex-shrink:0 \u2192 min-width:0, removing the desktop width floor", True),
            (".legend-item-coords", "new at base scope \u2014 promoted out of the \u22641080 block", True),
            (".topbar-actions header comment", "updated to name the rail", True),
            (".btn-header / .cast-group / .btn-cast", "unchanged \u2014 the working controls keep their size", False),
            ("\u22641080 block's Cast/Clear comment", "updated \u2014 How-it-Works no longer holds the row open", True),
        ],
        "blocks": [
            "flex-shrink:0 on the legend set a hard ~1353px minimum for the whole desktop header \u2014 every window between 1080 and that figure scrolled sideways",
            "min-width:0 lets the 250px address slot give way, so the address ellipsises above 1080 exactly as it already did below",
            ".legend-item-coords needs its OWN min-width:0 \u2014 as a flex container its automatic minimum is the whole nowrap address, which would re-impose the width the legend just gave up",
            "That rule already existed inside the \u22641080 block; it is promoted rather than duplicated",
            "The two comment edits keep the file honest: both described the old How-it-Works placement as load-bearing, and it no longer is",
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

  <p class="lede">A blog link, and a header that had room for it. The map header’s four meta buttons — Feedback, About, Donate and a stray How it Works — collapse into a <code>NavRail</code>: two enclosed segments of icon buttons, things to <em>read</em> then things to <em>do</em>, shared with the landing page. The glyphs are drawn to the app’s own grid (square caps, mitered joins, every mark built from a rectangle) rather than imported, and everything goes icon-only except Donate, which keeps its word and the map’s accent. The controls people actually came for are untouched. The right-hand column drops <strong>395px → 294px</strong>, which exposed and then closed a pre-existing overflow: the desktop layout had a hard ~1353px floor, because the legend was <code>flex-shrink: 0</code> and could never let its address ellipsise.</p>

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
    Generated from <code>changelog/changes-nav-rail.diff</code> by <code>changelog/build_nav_rail_report.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes.diff &amp;&amp; python changelog/build_nav_rail_report.py</code>.
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
