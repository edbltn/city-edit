#!/usr/bin/env python3
"""Generate the HTML changelog report from the captured unified diff.

Run from repo root: python changelog/build_report.py
Reads changelog/changes.diff, writes changelog/2026-08-09-donate-to-proposals.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-08-09-donate-to-proposals.html")

DATE = "2026-08-09"
TITLE = "Donate to a proposal"


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
        "id": "affordance",
        "tag": "THE FEATURE",
        "title": "One row at the foot of the card",
        "symptom": "Donating was a single site-wide button in the topbar. You could support City Edit, but you could not support the bike lane you had just clicked on \u2014 and the proposal modal is exactly where someone has decided they care about a specific block.",
        "cause": [
            "The proposal card is already dense: header, place, meta line, an optional column header, then one row per vote type carrying a top-proposal badge, a coverage fraction, source footnotes and a three-part vote control \u2014 plus four tool icons in the corner. A donate button competing with any of that would fight the vote controls, which are what the card is FOR",
            "The card's existing language is bracket tokens: <code>[a][b][c]</code> for sources, <code>[\u2212\u2502net\u2502+]</code> for votes, <code>[\u00d7]</code> for remove",
        ],
        "fixes": [
            "A single row at the foot, under a hairline: <strong>[$] Fund this proposal</strong> \u2014 the same bracket-token voice, so it reads as part of the card rather than a pasted-in donate button",
            "Quiet at rest (opacity 0.82, hairline border, the accent living on the <code>[$]</code> alone); the accent border and a 12% accent wash arrive on hover, at which point it is the thing being pointed at",
            "It is the card's only outbound money action and the only element that ASKS for something rather than reporting it \u2014 so it gets exactly one row, and nothing else moves",
        ],
        "files": ["client-react/src/styles/globals.css"],
    },
    {
        "id": "identity",
        "tag": "ATTRIBUTION",
        "title": "Making the donation say what it is for",
        "symptom": "A donate link that just opens the campaign page tells you nothing: the money arrives with no idea which proposal prompted it.",
        "cause": [
            "<code>donate.cityedit.org</code> redirects to a Donorbox campaign. Donorbox does have a \u201cleave a comment\u201d field \u2014 <code>donation[comment]</code> \u2014 but it does NOT prefill it from the URL: passing <code>?comment=\u2026</code> is ignored, and that field stays donor-authored",
            "It DOES prefill its UTM fields server-side. Fetching the embed with <code>?utm_content=X</code> comes back with <code>&lt;input value=\"X\" \u2026 name=\"donation[utm_params_attributes][utm_content]\"&gt;</code> already populated, and those values are stamped onto the donation record",
        ],
        "fixes": [
            "The proposal's identity rides the UTM parameters, which is the channel that actually reaches the record: <code>utm_content</code> is the message (\u201cFund: Add bus lane \u2014 Grand St &amp; Bowery \u2014 NYC Proposals\u201d) and <code>utm_term</code> is the deep link that re-selects that exact proposal on the map",
            "<code>utm_medium=proposal</code> is what separates a proposal-funding donation from a plain topbar one, which carries no UTM at all; <code>utm_campaign</code> is the map slug",
            "Each value is clipped at 160 chars with an ellipsis \u2014 ended legibly here rather than cut mid-word by something downstream",
            "The client links to <code>donate.cityedit.org</code>, never to Donorbox directly, so the campaign can move without a client deploy",
        ],
        "files": ["client-react/src/utils/donateLink.ts"],
    },
    {
        "id": "redirect",
        "tag": "INFRA \u00b7 WOULD HAVE SILENTLY FAILED",
        "title": "The redirect was dropping the whole query string",
        "symptom": "Every parameter above would have vanished in transit. <code>curl -sI 'https://donate.cityedit.org/?utm_source=cityedit'</code> returns a Location with no query at all.",
        "cause": [
            "nginx does not append the query string to a <code>return 301</code> whose target is a literal URL \u2014 only to rewrites and to targets containing variables. The redirect inventory in <code>docs/url-routing.md</code> even recorded this, in a column reading <strong>dropped</strong>",
            "Nothing would have errored. The donation page would have opened normally and every donation would simply have arrived anonymous \u2014 the failure mode is silence, which is why it is worth calling out",
        ],
        "fixes": [
            "<code>return 301 https://donorbox.org/support-spherical-harmonics$is_args$args;</code>, with a comment at the line saying what depends on it",
            "The redirect inventory row flipped from <em>dropped</em> to <em>preserved</em> \u2014 it is the stated source of truth for every redirect the system performs, so a stale row there is a trap for the next person",
            "<strong>This ships with the image</strong>: until the nginx config is deployed, the fund links work but arrive stripped of their attribution",
        ],
        "files": ["deploy/nginx-cloudrun.conf", "docs/url-routing.md"],
    },
    {
        "id": "rule",
        "tag": "CORRECTNESS",
        "title": "Refusing to guess which proposal",
        "symptom": "\u201cFund this proposal\u201d has to name ONE proposal. A busy intersection card lists several vote types, and a card on an unvoted segment lists none.",
        "cause": [
            "The card is headed by a proposal only when it has a winner \u2014 a top proposal (PBTP) for a point card, a covered corridor (RBTP) for the route-summary card. Otherwise it is a place with N proposals on it, and the header shows no label",
            "Station-network maps (e-bikes) suppress the top-proposal header entirely \u2014 their pins ARE the stations \u2014 so the winner the fund row may offer has to be the same gated value the header uses, not the raw one",
        ],
        "fixes": [
            "<code>fundableProposalName</code>: the winner if there is one; else a lone vote type; else the empty string and NO fund row \u2014 wrong is worse than absent",
            "The station gate is now computed once as <code>pinnedCardWinner</code> and used for the header, the eyebrow and the fund row together, so they cannot disagree. The share link's <code>?vt</code> deliberately keeps naming the raw winner, which is pre-existing behavior",
            "Hover cards never get the row: they are <code>pointer-events: none</code>, so the link would render and not be clickable",
        ],
        "files": ["client-react/src/utils/donateLink.ts", "client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
    {
        "id": "leaflet",
        "tag": "BUG FOUND EN ROUTE",
        "title": "The source footnotes were Leaflet blue",
        "symptom": "The new row rendered in browser-link blue instead of the card's ink. Measuring it showed the same was already true of the <code>[a][b][c]</code> source markers shipped on 08-01 \u2014 described in their own comment as \u201cdeliberately small and quiet\u201d, and rendering as bright blue links.",
        "cause": [
            "Leaflet's stylesheet carries <code>.leaflet-container a { color: #0078A8 }</code> \u2014 specificity 0-2-0. These cards are portaled INTO the map container, so every anchor inside them matches it",
            "<code>.graph-proposal-row-link { color: inherit }</code> is 0-1-0 and simply loses. Nobody noticed because the card's other \u201clink\u201d (Street View) draws an SVG with a hardcoded fill",
        ],
        "fixes": [
            "One reset above Leaflet's reach: <code>.leaflet-container .graph-proposal-card a { color: inherit }</code>",
            "That fixes the source markers and is also what lets the fund row take the card's own ink \u2014 it expresses its rest/hover difference with <code>opacity</code> rather than a second color declaration that would need to win the same fight",
        ],
        "files": ["client-react/src/styles/globals.css"],
    },
]


VERIFY = [
    "Donorbox's prefill behavior was <strong>measured, not assumed</strong>: fetching the embed with <code>?utm_content=TESTMSG123</code> returns that value already set on <code>donation[utm_params_attributes][utm_content]</code>, while <code>?comment=HELLOMSG</code> appears nowhere in the response.",
    "The dropped query string was confirmed against the live host, not read off the config: <code>curl -sI 'https://donate.cityedit.org/?utm_source=cityedit&amp;utm_content=TEST'</code> returns a Location with no query.",
    "Driven in the running app on <code>nyc-proposals</code>: clicking a top-proposal pin opens a TOP PROPOSAL card whose fund row's href resolves to <code>donate.cityedit.org</code> with <code>utm_content=\u201cFund: Fix dangerous intersection \u2014 40.75732, -73.98965 \u2014 NYC Proposals\u201d</code>, <code>utm_medium=proposal</code>, <code>utm_campaign=nyc-proposals</code> and a <code>utm_term</code> deep link carrying the vote type.",
    "The route side verified the same way: selecting the 30th Avenue corridor opens <strong>TOP ROUTE PROPOSAL \u2014 Add bus lane</strong> (72 blocks) and the fund row is present there too.",
    "Both negative cases seen in the app: a segment with no votes renders \u201cNo votes yet\u201d and <strong>no fund row</strong> (checked by DOM query, <code>rows=0 fund=false</code>).",
    "The remaining branch \u2014 several proposals on one place \u2014 is covered by unit test rather than by screenshot, which is the more durable place for it. <strong>10 tests pass</strong> across the URL builder and the fundable-name rule.",
    "The Leaflet-blue bug was measured, not eyeballed: a probe anchor with class <code>graph-proposal-row-link</code> inserted into a live card computes to <code>rgb(0, 120, 168)</code>. After the fix, the <code>[a]</code> marker on the bus-lane row reads as grey.",
    "<code>npx tsc -b</code> clean \u2014 Vite dev never type-checks, so this is the only thing standing between an edit and a broken client build.",
    "Committed as a <strong>partial stage</strong>: another session is mid-flight in the same two files (a legend vote-type filter and a nav rail), so only this workstream's hunks were applied to the index and <code>git diff --cached</code> was read back line by line before committing.",
]

CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-proposals</code> and click a proposal pin \u2014 expect a <strong>[$] Fund this proposal</strong> row at the foot of the card, and an accent border when you hover it.",
    "Click that row and check the Donorbox page opens in a NEW tab with your map selection still behind it.",
    "On the opened donation URL, confirm the query string survives the redirect \u2014 this is the part that needs the nginx change deployed before it works in prod.",
    "Click a plain street segment with no votes and confirm there is <strong>no</strong> fund row; then click a busy intersection listing several vote types and confirm there is none there either.",
    "Look at a row with source footnotes (<code>[a]</code>) and confirm they now read as quiet grey rather than link blue.",
    "Run <code>cd client-react &amp;&amp; npx vitest run src/utils/donateLink.test.ts</code> \u2014 10 tests.",
    "<strong>Before trusting attribution in prod</strong>: deploy the image (the nginx conf ships with it), then re-run the curl above against <code>donate.cityedit.org</code> and confirm the Location now carries the query.",
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
    "client-react/src/utils/donateLink.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 utils", "Small pure helpers shared across the map UI \u2014 share links, vote keys, geocoding, telemetry. This is the new one: what a donation targets, and the URL that says so"),
        "file": ("donateLink.ts", "~85 LOC \u2014 the donation host, the UTM message builder, and the fundable-name rule"),
        "outline": [
            ("header comment", "new \u2014 records that Donorbox ignores comment= but stamps UTM server-side, which is the whole reason for the design", True),
            ("DONATE_URL / MAX_UTM_CHARS / clip", "new \u2014 our own host (never Donorbox directly) + a 160-char legible clip", True),
            ("fundableProposalName", "new \u2014 winner, else a lone vote type, else nothing", True),
            ("ProposalDonationTarget", "new \u2014 name / place / deep link", True),
            ("buildProposalDonateUrl", "new \u2014 assembles the five UTM parameters", True),
        ],
        "blocks": [
            "comment= is NOT prefillable \u2014 verified against the live embed; the message has to ride utm_content instead",
            "utm_term carries the deep link back to the exact selection, so the record links to the proposal and not just names it",
            "utm_medium=proposal is what distinguishes these from topbar/landing donations, which carry no UTM at all",
            "The message is written to be read cold in the donation record, because that record is the only place it is ever read",
            "getCurrentMap()/getMapSlug() are read at click time, so the link always names the map you are actually on",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 graph layer", "The map's whole interactive surface: topology decode, heat paint, hit-testing, top-proposal pins and the proposal cards \u2014 including ProposalCard, shared by the hover preview and the pinned/route modals"),
        "file": ("GraphLayer.tsx", "~5,100 LOC \u2014 the largest component in the client; this change touches only the card's inputs and its footer"),
        "outline": [
            ("imports", "+1 line \u2014 the donation helpers", True),
            ("topology decode / heat paint / hit-testing", "unchanged", False),
            ("pinned card content (pinnedName, pinnedVoteTypes, pinnedWinner)", "unchanged", False),
            ("pinnedCardWinner / pinnedShareUrl / pinnedDonate", "new \u2014 the station gate computed once, then the share + fund links off it", True),
            ("route-summary derivations (coveredRouteProposal)", "unchanged", False),
            ("routeFundName / routeDonate", "new \u2014 the corridor's fund target", True),
            ("indicator markers / RBTP diamonds", "unchanged", False),
            ("<ProposalCard> pinned + route call sites", "donate= passed; winner/eyebrow now read the gated value", True),
            ("ProposalCardProps", "+1 prop \u2014 donate", True),
            ("ProposalCard body", "+1 footer row \u2014 the fund link, interactive-only", True),
        ],
        "blocks": [
            "pinnedCardWinner gates the station-network case ONCE \u2014 header, eyebrow and fund row can no longer disagree about whether the card names a proposal",
            "The share link's ?vt deliberately keeps the ungated winner: changing it would alter existing deep links on station maps",
            "interactive && donate \u2014 hover cards are pointer-events:none, so a link there would be visible and dead",
            "target=_blank + rel=noopener: the donation page is outside the app and must never navigate the map away from an open selection",
            "onClick stopPropagation mirrors the source links \u2014 a click on the row must not reach the map and re-select",
            "The route card passes no place: a corridor spans many blocks and has no one street name, so the deep link locates it instead",
        ],
    },
    "client-react/src/styles/globals.css": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 global styles", "The single stylesheet: design tokens, the themeable palette, topbar, map controls, markers, and the proposal card's whole visual language"),
        "file": ("globals.css", "~2,900 lines \u2014 tokens first, then layout, then the card system; this change adds one reset and one component"),
        "outline": [
            ("design tokens (:root) + palette", "unchanged \u2014 --accent / --ink / --hairline are what the new row reads", False),
            ("topbar / legend / map controls", "unchanged", False),
            ("graph-indicator-modal + proposal card", "unchanged", False),
            (".graph-proposal-row-links / -link", "context \u2014 the footnote markers this reset repairs", False),
            (".leaflet-container .graph-proposal-card a", "new \u2014 the reset that outranks Leaflet's link blue", True),
            (".graph-proposal-fund / -token", "new \u2014 the fund row and its accent glyph", True),
            (".graph-vote (\u2212\u2502net\u2502+)", "unchanged \u2014 the control the fund row must not compete with", False),
        ],
        "blocks": [
            "Leaflet's .leaflet-container a is 0-2-0 and beats any single class on a card link \u2014 the reset has to be 0-3-0 to win",
            "The reset is why the fund row can express rest vs hover with opacity instead of a color that would have to win the same fight again",
            "opacity 0.82 at rest: present but under the vote controls, which are what the card is for",
            "The accent lives on the [$] alone \u2014 one bright glyph on a grey row draws the eye without shouting",
            "radius 0, monospace, hairline border \u2014 the row is built from the same primitives as [a][b][c] and [\u2212\u2502net\u2502+]",
        ],
    },
    "deploy/nginx-cloudrun.conf": {
        "on": ["nginx"],
        "module": ("Deploy \u00b7 nginx", "The Cloud Run reverse proxy: static assets, tile caching, the per-subdomain server blocks, and the app upstream"),
        "file": ("nginx-cloudrun.conf", "~200 lines \u2014 caches, the donate/feedback subdomain blocks, then the catch-all app server"),
        "outline": [
            ("tile proxy_cache_path", "unchanged", False),
            ("server donate.cityedit.org", "the 301 now forwards the query string", True),
            ("server feedback.cityedit.org", "unchanged", False),
            ("catch-all app server", "unchanged", False),
        ],
        "blocks": [
            "nginx appends the query to a `return` target only when it is a rewrite or contains variables \u2014 a literal URL drops it",
            "$is_args$args is the idiom: emits nothing when there is no query, ?\u2026 when there is",
            "The failure this prevents is SILENT \u2014 the donation page opens fine, it just arrives anonymous",
            "This ships with the app image; the client change is live before it, so attribution starts working only after the next deploy",
        ],
    },
    "docs/url-routing.md": {
        "on": ["nginx", "Flask API", "React / Leaflet client"],
        "module": ("Docs \u00b7 URL routing", "How a URL becomes a map: slugs, subdomains, retired-slug redirects, and the redirect inventory that is the stated source of truth"),
        "file": ("url-routing.md", "~230 lines \u2014 slug rules, rename procedure, then the redirect inventory table"),
        "outline": [
            ("slug + subdomain rules", "unchanged", False),
            ("rename procedure", "unchanged", False),
            ("Redirect inventory table", "the donate row's query-param column flips dropped \u2192 preserved", True),
            ("map_redirects rows", "unchanged", False),
        ],
        "blocks": [
            "The table declares itself the source of truth \u2014 a stale row is a trap for whoever reads it next",
            "The row now says WHY the query is preserved, so nobody 'simplifies' the $is_args$args back out",
        ],
    },
    "client-react/src/utils/donateLink.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 utils tests", "Vitest suites beside the helpers they cover \u2014 vote keys, block selection, sparse votes, and now the donation link"),
        "file": ("donateLink.test.ts", "~80 LOC \u2014 10 tests over the URL builder and the fundable-name rule"),
        "outline": [
            ("map/runtime mock", "new \u2014 pins the map name + slug so the message is exact", True),
            ("buildProposalDonateUrl suite", "new \u2014 host, message, deep link, tagging, absent place, clipping", True),
            ("fundableProposalName suite", "new \u2014 winner / lone type / several / none", True),
        ],
        "blocks": [
            "Asserts the link points at donate.cityedit.org, NOT the payment processor \u2014 that indirection is deliberate and easy to lose",
            "The absent-place case checks there is no dangling separator, which is the thing a naive join gets wrong",
            "The clip test asserts both the ceiling and the ellipsis, so a silent mid-word cut would fail",
            "'declines to guess between several proposals' is the branch the browser run could not reach \u2014 it lives here instead",
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

  <p class="lede">A proposal can now be donated to. Its card carries one row at the foot — <code>[$] Fund this proposal</code> — in the same bracket-token voice as the <code>[a][b][c]</code> source markers and the <code>[−│net│+]</code> control, so a dense modal gains an action rather than a section. The link carries WHAT is being funded: Donorbox will not prefill its comment box from a URL, but it does stamp UTM parameters onto the donation record, so the message rides <code>utm_content</code> and a deep link back to the exact proposal rides <code>utm_term</code>. All of which nginx was silently dropping — a literal <code>return 301</code> does not forward the query string.</p>

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
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_donate_proposals_report.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes.diff &amp;&amp; python changelog/build_donate_proposals_report.py</code>.
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
