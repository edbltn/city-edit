#!/usr/bin/env python3
"""Generate the HTML changelog report from the captured unified diff.

Run from repo root: python changelog/build_report.py
Reads changelog/changes.diff, writes changelog/2026-08-09-donate-followups.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-08-09-donate-followups.html")

DATE = "2026-08-09"
TITLE = "Fund per row \u2014 and three things that were not what they looked like"


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
        "id": "chip",
        "tag": "THE CHANGE",
        "title": "Fund moves onto the row",
        "symptom": "The fund affordance shipped as a text row at the foot of the card \u2014 <strong>[$] Fund this proposal</strong> \u2014 which is a whole extra band of card for one link, and it had to answer a question first: WHICH proposal, when the card lists several?",
        "cause": [
            "A card is a place; a ROW is a proposal. Putting the action on the card forced a rule (<code>fundableProposalName</code>: the top proposal, else a lone vote type, else nothing) whose entire job was to disambiguate something the rows already disambiguate by existing",
            "That rule also meant the commonest interesting case \u2014 a corner where three different things have been proposed \u2014 got NO fund affordance at all, because guessing would have been wrong",
        ],
        "fixes": [
            "The row carries a <code>[$]</code> chip beside its own <code>[a][b][c]</code> source markers: same bracket-token vocabulary, same size, in the accent because it is an ACTION and they are citations",
            "Each chip's link names its own vote type, and its deep link re-selects this point under that type \u2014 so two rows on one corner produce two distinguishable donations",
            "The chip LEADS the marker run instead of trailing it: citation counts vary row to row, so a chip after them would land at a different x on every line, and this is a thing you aim at",
            "The card-level <code>donate</code> prop becomes <code>donateUrlFor(label)</code>, and <code>fundableProposalName</code> is deleted outright \u2014 the question it answered no longer exists",
        ],
        "files": ["client-react/src/components/GraphLayer/GraphLayer.tsx", "client-react/src/utils/donateLink.ts"],
    },
    {
        "id": "tooltip",
        "tag": "NOT WHAT IT LOOKED LIKE",
        "title": "The nav tooltips were not losing a z-index fight",
        "symptom": "Hovering Donate (and How it Works) showed a label that seemed to disappear underneath the &quot;Clear&quot; button on the row below.",
        "cause": [
            "It reads exactly like a stacking bug, but it is not one. The tooltip is <code>position: absolute</code> at <code>--z-dropdown</code> (200); nothing between it and the topbar creates a stacking context, and <strong>a positive-z positioned element paints above every non-positioned sibling in the same context</strong>. Painting a probe copy in magenta showed it sitting cleanly on top of Clear",
            "The real cause is camouflage. The tooltip was <code>background: var(--paper)</code> with a <code>1px solid var(--ink)</code> border \u2014 which is exactly what the Cast/Clear buttons it lands on are. Same fill, same hairline, so the box read as part of the control beneath it and its text tangled with the word &quot;Clear&quot;",
            "Donate makes it worse by having nothing to say: it renders its label INLINE above 1080px, so its tooltip was a second &quot;Donate&quot;, one row lower, on top of something else",
        ],
        "fixes": [
            "The tooltip inverts \u2014 <code>background: var(--ink)</code>, <code>color: var(--paper)</code> \u2014 so it can never be mistaken for the UI it covers. Inversion is theme-safe by construction: both tokens flip together on the light basemap",
            "Donate's tooltip is suppressed above 1080px (<code>content: none</code>), exactly where its inline label speaks for it; below that width the label is hidden and the tooltip comes back",
            "z-index is untouched, because it was never the problem",
        ],
        "files": ["client-react/src/components/NavRail/NavRail.css"],
    },
    {
        "id": "mobile",
        "tag": "MOBILE",
        "title": "The source markers were there the whole time",
        "symptom": "Reported as &quot;the [a][b][c] links do not appear on mobile&quot;.",
        "cause": [
            "They do appear. Driven headless at 390\u00d7844 with touch emulation and a real tap on a proposal pin: the interactive card opens, <code>/api/vote-sources</code> answers with the 21st Street study, and the anchor is in the DOM \u2014 <code>display: flex</code>, <code>visibility: visible</code>, inside the card's bounds",
            "What is missing on touch is the HOVER. The markers rest at <code>opacity: 0.5</code> and only reach full strength on <code>:hover</code>, which a finger never delivers. So on a phone they sit permanently at half strength, at 10px",
            "10px at 50% opacity on a phone is not a subtle affordance, it is an invisible one \u2014 which is indistinguishable from absent",
        ],
        "fixes": [
            "Under <code>@media (hover: none)</code> the markers rest at 0.85 instead of 0.5 \u2014 the state they can never otherwise reach",
            "The whole run (fund chip included) goes to 11px with 4px of vertical padding, which takes the tap target from 13px to 22px tall without widening the row or moving anything on desktop",
        ],
        "files": ["client-react/src/styles/globals.css"],
    },
    {
        "id": "donorbox",
        "tag": "INVESTIGATION",
        "title": "What Donorbox will and will not prefill",
        "symptom": "The donation links looked like they populated nothing at all.",
        "cause": [
            "<strong>The live redirect is still eating the query string.</strong> <code>curl -sI 'https://donate.cityedit.org/?utm_source=cityedit'</code> returns a Location with no query today \u2014 the <code>$is_args$args</code> fix is committed but ships with the app image, and prod has not been deployed since. Nothing reaches Donorbox to prefill",
            "<strong>The comment field genuinely cannot be prefilled.</strong> Probed against the live embed one parameter at a time: <code>comment</code>, <code>donation[comment]</code>, <code>message</code>, <code>note</code> and <code>designation</code> all come back with the field untouched",
            "What DOES prefill, verified the same way: <code>first_name</code>, <code>email</code>, <code>amount</code> \u2014 and the five UTM parameters, which land server-side in <code>donation[utm_params_attributes][\u2026]</code> and ride onto the donation record",
        ],
        "fixes": [
            "No code change: the design already uses the only channel Donorbox honours. The message rides <code>utm_content</code> and the deep link rides <code>utm_term</code>, and both are stamped into the record",
            "The blocker is a deploy, not a bug \u2014 after the image ships, the same curl should return a Location that carries the query",
            "Designations are not a way around it: they are a fixed list configured in the Donorbox dashboard, so they cannot name one of thousands of proposals",
        ],
        "files": [],
    },
]


VERIFY = [
    "The z-index theory was <strong>disproved before anything was changed</strong>: a probe copy of the tooltip painted in magenta sits fully on top of the Clear button. The fix is a colour change, not a stacking change.",
    "Mobile was measured rather than assumed, twice over \u2014 once by deep link and once by a real <code>touchscreen.tap</code> on a pin at 390\u00d7844 with <code>hasTouch</code>. Both open the interactive card, both resolve sources, both render the anchor.",
    "Touch now reports <code>opacity 0.85</code>, <code>font-size 11px</code>, a <strong>22px</strong> tap height; desktop is unchanged at 0.5 / 10px / 13px.",
    "The chip renders in the accent on both: <code>rgb(192, 103, 74)</code> against this map's terracotta, with <code>utm_content = \u201cFund: Add bus lane \u2014 NYC Proposals\u201d</code> and the host still <code>donate.cityedit.org</code>.",
    "Donorbox's prefill surface was enumerated by probe, not by documentation: 10 candidate parameters fetched one at a time against the live embed, and only first_name / email / amount / UTM come back populated.",
    "Full client suite green \u2014 <strong>409 passed, 1 skipped</strong> \u2014 and <code>npx tsc -b</code> clean.",
    "Seen in the app: the row reads <code>\u25c6 Add bus lane [$] [a] 72/72 [\u22120\u25021\u2502+1]</code>, and the inverted Feedback tooltip is legible sitting directly over Cast/Clear.",
]

CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-proposals</code>, click a proposal \u2014 expect a small accent <code>[$]</code> right after the vote-type name, before any <code>[a]</code> markers, and no row at the foot of the card.",
    "Click a corner with SEVERAL vote types and confirm every row has its own chip \u2014 that is the case the old footer row had to skip.",
    "Hover a nav-rail icon (Blog / About / Feedback) and confirm the label is now a dark inverted chip that reads clearly over Cast/Clear.",
    "Hover <strong>Donate</strong> at a wide window and confirm NO tooltip appears (its label is already showing); narrow the window under 1080px and confirm the label hides and the tooltip returns.",
    "On a phone (or a touch-emulated window), open a proposal on <code>nyc-proposals</code> and confirm the <code>[a]</code> markers are legible now rather than ghost-faint.",
    "<code>curl -sI 'https://donate.cityedit.org/?utm_source=cityedit'</code> \u2014 expect the query to still be DROPPED until the image is deployed; that is the outstanding item, not a code bug.",
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
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 graph layer", "The map's whole interactive surface: topology decode, heat paint, hit-testing, top-proposal pins and the proposal cards \u2014 including ProposalCard, shared by the hover preview and the pinned/route modals"),
        "file": ("GraphLayer.tsx", "~5,100 LOC \u2014 this change moves the fund affordance from the card's footer onto its rows"),
        "outline": [
            ("imports", "\u2212 fundableProposalName, now deleted", True),
            ("topology decode / heat paint / hit-testing", "unchanged", False),
            ("pinnedCardWinner / pinnedShareUrl", "unchanged \u2014 still what the header and copy-link read", False),
            ("pinnedDonateUrlFor", "replaces pinnedDonate \u2014 a per-label URL builder, not one card-wide link", True),
            ("routeDonateUrlFor", "replaces routeDonate \u2014 same shape for the corridor card", True),
            ("<ProposalCard> call sites", "donate= \u2192 donateUrlFor=", True),
            ("ProposalCardProps", "the prop becomes a function of the row's label", True),
            ("ProposalCard row map", "+ the [$] chip, ahead of the source markers", True),
            ("ProposalCard footer", "\u2212 the whole fund row", True),
        ],
        "blocks": [
            "A row IS a proposal \u2014 which is why the disambiguation rule could be deleted rather than refined",
            "fundHref is gated on `interactive`, exactly like sources: hover cards are pointer-events:none, so a chip there would be visible and dead",
            "The pinned card rebuilds its deep link PER LABEL (buildSelectionUrl(point, label)), so the link re-selects this point under that row's own vote type",
            "The route card passes no place: a corridor spans many blocks, so the live URL is what locates it",
            "The chip is emitted BEFORE the [a][b][c] run so its position is stable down the card",
        ],
    },
    "client-react/src/components/NavRail/NavRail.css": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 nav rail", "The topbar's quiet half: How it Works \u00b7 Blog \u00b7 About | Feedback \u00b7 Shop \u00b7 Donate, icon-only except Donate"),
        "file": ("NavRail.css", "~180 lines \u2014 rail/group/button geometry, the Donate accent, the hover tooltips, then the breakpoints"),
        "outline": [
            (".nav-rail / .nav-group / .nav-btn", "unchanged", False),
            (".nav-btn-donate", "unchanged \u2014 the accent fill", False),
            ("@media (hover: hover) \u2192 .nav-btn::after", "INVERTED: ink fill, paper text", True),
            ("\u2514 @media (min-width: 1081px) \u2192 .nav-btn-donate::after", "new \u2014 content: none where the inline label already says it", True),
            ("edge-anchored last tooltip", "unchanged", False),
            ("@media (max-width: 1080px) \u2014 label hidden", "unchanged \u2014 the breakpoint the suppression is keyed to", False),
        ],
        "blocks": [
            "z-index is NOT touched: at --z-dropdown the tooltip already paints above the row it lands on \u2014 proved with a magenta probe",
            "The bug was that paper-on-hairline is exactly what Cast/Clear are, so the label dissolved into them",
            "Inversion is theme-safe: --ink and --paper swap together on the light basemap, so the chip stays opposite its surroundings",
            "The suppression breakpoint MUST track the label's own (1080px) \u2014 if they drift, Donate loses its name at some width",
        ],
    },
    "client-react/src/styles/globals.css": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 global styles", "Design tokens, the themeable palette, topbar, map controls, markers, and the proposal card's whole visual language"),
        "file": ("globals.css", "~2,900 lines \u2014 this change swaps the fund row for a chip and repairs the marker run on touch"),
        "outline": [
            (".graph-proposal-row-links", "unchanged \u2014 the run the chip joins", False),
            (".leaflet-container .graph-proposal-card a", "unchanged \u2014 the Leaflet-blue reset", False),
            (".graph-proposal-row-link / :hover", "unchanged", False),
            (".graph-proposal-fund + -token", "REMOVED \u2014 the footer row's styles", True),
            (".graph-proposal-row-fund", "new \u2014 the chip", True),
            (".leaflet-container \u2026 .graph-proposal-row-fund", "new \u2014 the accent, at the reset's own weight", True),
            ("@media (hover: none)", "new \u2014 rest state + tap target for the whole run", True),
            (".graph-vote", "unchanged", False),
        ],
        "blocks": [
            "The chip's colour needs the reset's own 0-3-0 selector: a plain class would be flattened back to card ink by the Leaflet fix from the first round",
            "Rule ORDER is load-bearing: the (hover: none) block sits last so its 11px wins back from the base rule at equal specificity",
            "0.85 rather than 1.0 on touch \u2014 still a footnote, just a legible one",
            "padding: 4px 0 buys tap height (13px \u2192 22px) without widening the row or touching desktop",
        ],
    },
    "client-react/src/utils/donateLink.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 utils", "What a donation targets and the URL that says so"),
        "file": ("donateLink.ts", "~70 LOC after the deletion"),
        "outline": [
            ("header comment", "unchanged \u2014 still the record of what Donorbox does and does not prefill", False),
            ("DONATE_URL / MAX_UTM_CHARS / clip", "unchanged", False),
            ("fundableProposalName", "DELETED \u2014 per-row chips have nothing to disambiguate", True),
            ("buildProposalDonateUrl", "unchanged \u2014 now called once per row instead of once per card", False),
        ],
        "blocks": [
            "The deletion is the point: the rule existed only because the action sat on the card",
            "The probe results the header comment records were re-confirmed this round across 10 candidate parameters",
        ],
    },
    "client-react/src/utils/donateLink.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 utils tests", "Vitest suites beside the helpers they cover"),
        "file": ("donateLink.test.ts", "~70 LOC \u2014 7 tests"),
        "outline": [
            ("map/runtime mock", "unchanged", False),
            ("buildProposalDonateUrl suite", "unchanged \u2014 host, message, deep link, tagging, absent place, clipping", False),
            ("fundableProposalName suite", "REPLACED by a per-row-target suite", True),
        ],
        "blocks": [
            "The new test asserts two rows on the SAME place produce different utm_content \u2014 the property the move onto the row is for",
            "The deleted suite tested a rule that no longer exists; keeping it would have tested nothing real",
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

  <p class="lede">Four follow-ups. The fund affordance moves off the card and onto the row: a <code>[$]</code> chip beside that row's <code>[a][b][c]</code> source markers, which also lets the &quot;which proposal did you mean&quot; rule be deleted rather than refined \u2014 a row IS a proposal. The other three were misdiagnosed by their symptoms. The nav tooltip was not losing a z-index fight; it already painted on top, in the same paper-and-hairline as the buttons it landed on, so it dissolved into them. The mobile source markers were not missing; they were sitting at the half-strength resting state that only a hover can lift, at 10px, which on a phone is the same as gone. And the donation links populate nothing because the redirect still eats the query string in prod \u2014 a deploy, not a bug. The one thing that really cannot be done is the comment field: probed one parameter at a time, Donorbox ignores every spelling of it.</p>

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
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_donate_followups_report.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes.diff &amp;&amp; python changelog/build_donate_followups_report.py</code>.
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
