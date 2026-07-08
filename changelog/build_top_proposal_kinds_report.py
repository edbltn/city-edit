#!/usr/bin/env python3
"""Generate the top-proposal kinds + spacing + corridor-length changelog report.

Run from repo root: python changelog/build_top_proposal_kinds_report.py
Reads changelog/changes-top-proposal-kinds.diff,
writes changelog/2026-07-08-top-proposal-kinds.html

Modeled on build_forced_corridor_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-top-proposal-kinds.diff")
OUT_PATH = os.path.join(HERE, "2026-07-08-top-proposal-kinds.html")

DATE = "2026-07-08"
TITLE = "Top-proposal rework — route/point kinds end-to-end, one pin per block, corridors earn their length"


def split_by_file(diff_text: str):
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
        elif raw.startswith("diff ") or raw.startswith("index "):
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
        "id": "kinds",
        "tag": "Data model · vote-type kinds",
        "title": "1 · Every vote type now carries a route/point kind — recorded, served, repaired",
        "symptom": (
            "On NYC Bikes, ROUTE-flavored vote types (“Add bike greenway”, “Add bike lane”) showed up as "
            "square POINT top-proposal pins. Presets always declared a pointType, but the flag died at the "
            "config boundary: the global vote_types registry (which backs free-text suggestions and the "
            "searched custom types) never stored it, the cast that creates a suggestion never sent it, and "
            "the proposal selectors never looked at it."
        ),
        "cause": [
            "Four half-unified code paths: preset lists carried <code>pointType</code> in JSONB; the "
            "<code>vote_types</code> table had only (id, label); <code>/api/vote</code> dropped the kind on "
            "the floor; <code>searchVoteTypes</code> served bare label strings.",
        ],
        "fixes": [
            "<code>vote_types.point_type</code> column ('route' | 'point' | NULL). "
            "<code>get_or_create_vote_type_id(label, point_type)</code> stamps NEW rows and fills NULLs "
            "(<code>COALESCE</code> — an existing kind is never overwritten by a later cast).",
            "<code>seed_presets()</code> force-stamps preset labels (presets are authoritative — this is the "
            "startup repair for mis-flagged/legacy preset rows like “Add bike greenway”).",
            "NEW <code>backfill_vote_type_kinds()</code> (runs on every startup): fills NULL rows by label "
            "match against every authored set — preset lists, promoted community lists, and each map's "
            "inline <code>custom_vote_types</code>. Local run: 50 rows stamped, 4 true free-text legacy "
            "suggestions remain NULL (unknown kind ⇒ eligible for both families, never hidden).",
            "<code>/api/vote</code> accepts <code>point_type</code>; the client sends the selection's kind "
            "(<code>castVotes({pointType})</code> from RouteContext) — the creating cast is the only "
            "witness of what a free-text suggestion was suggested as.",
            "<code>searchVoteTypes</code> upgraded from <code>string[]</code> to "
            "<code>{label, pointType}[]</code>; map creation 400s on custom vote types without a valid "
            "<code>pointType</code> and registers the authored kinds immediately.",
            "NEW client resolver <code>pointTypeForLabel(label, voteTypes, searchVoteTypes)</code> — mirrors "
            "<code>iconForLabel</code>'s precedence (map's own list → searched customs → preset themes → "
            "null). The selector's searched suggestions are now kind-filtered too (a route-kind custom type "
            "no longer offers itself for a point selection).",
        ],
        "files": [
            "server/database.py", "server/app.py", "server/vote_store.py",
            "client-react/src/themes.ts", "client-react/src/map/runtime.ts",
            "client-react/src/components/VoteTypeSelector/VoteTypeSelector.tsx",
            "client-react/src/utils/castVote.ts", "client-react/src/context/RouteContext.tsx",
        ],
    },
    {
        "id": "pbtp",
        "tag": "PBTP · selection",
        "title": "2 · Point pins: point-kind only, one per block, same-type pins 600 m apart",
        "symptom": (
            "Two (or more) square pins could stack on a single street block when their vote types "
            "differed, same-type pins could sit one avenue block apart (300 m), and route-kind types "
            "competed for point pins at all."
        ),
        "cause": [
            "selectTopProposals had no notion of kind, no block awareness (only exact-edge dedupe), and a "
            "single 300 m same-type radius.",
        ],
        "fixes": [
            "Step 1 filter: <code>computeVoteTypeWinners(…, kindOf)</code> skips ROUTE-kind labels — their "
            "support surfaces as RBTP diamonds instead. Unknown-kind labels stay eligible. Station "
            "networks pass no resolver (every vote there is a point).",
            "NEW step 3 <code>dedupeWinnersByBlock</code>: at most ONE pin per street block ACROSS all "
            "vote types (strongest wins, salted tiebreak). Uses <code>blockKeyOf</code>, so maps without "
            "block artifacts degrade to per-edge singleton keys — no behavior change there.",
            "Same-type spacing raised 300 → 600 m (<code>TOP_PROPOSAL_MIN_SPACING_M</code>): identical "
            "pins carry zero information nearby, while different-type pins only collapse when they share "
            "a block — the cross-type grain is deliberately finer than the same-type radius.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/topProposals.ts",
            "client-react/src/components/GraphLayer/topProposals.test.ts",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
        ],
    },
    {
        "id": "rbtp",
        "tag": "RBTP · corridor length",
        "title": "3 · Route corridors earn their length — support-scaled meter budget",
        "symptom": (
            "Route-based top proposals could snake for miles: greedy path extension keeps going while ANY "
            "net-positive arc exists, so one seed vote plus a chain of stray net-1 edges produced an "
            "absurdly long “corridor”."
        ),
        "cause": [
            "No length limit anywhere in the peel pipeline — the only gates were score and edge COUNT "
            "minimums, which long paths pass trivially.",
        ],
        "fixes": [
            "Each peeled path gets a meter budget that grows with support: "
            "<code>min(2500, 600 + 150·√score)</code> (<code>routeLengthBudgetM</code>). One block of "
            "votes ⇒ a short corridor; 4× the votes ⇒ 2× the earned reach.",
            "NEW <code>capPathToLengthBudget</code>: sliding window over the path's edges keeping total "
            "length ≤ budget, returning the max-weight window — the hottest contiguous stretch survives, "
            "straggly tails drop. Deterministic ties (shorter, then earliest); a single over-budget edge "
            "(a bridge) is kept rather than trimming to nothing. Activity gates run on the TRIMMED path.",
            "Point-kind vote types are skipped by the corridor extraction (<code>opts.kindOf</code>) — the "
            "mirror of the PBTP filter.",
            "NEW <code>edgeLengthMeters</code> in graphTopology (equirectangular, shared helper).",
            "Fixed a latent bug the window slicing exposed: <code>greedyHeaviestPath</code>'s splice "
            "duplicated the seed node in <code>nodes</code>, misaligning <code>nodes[i]</code> ↔ "
            "<code>edges[i]</code>. Harmless before (only the endpoints were read), wrong anchors after "
            "trimming.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/routeProposals.ts",
            "client-react/src/components/GraphLayer/routeProposals.test.ts",
            "client-react/src/components/GraphLayer/graphTopology.ts",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
        ],
    },
]

VERIFY = [
    "Client: <code>tsc --noEmit</code> clean; full vitest suite 231/231 green (12 files) — including "
    "16 new tests: kind filters both families, block dedupe (cross-type collapse, singleton-key "
    "degrade, salted ties), budget growth/clamp, window trimming (hot core kept, anchors follow the "
    "trim, ties, over-budget single edge).",
    "Server: <code>pytest tests/unit</code> 47/47 green; app.py/database.py/vote_store.py parse clean.",
    "Live migration on local dev DB: startup stamped point_type on 50 vote_types rows "
    "(25 route / 25 point); 4 legacy free-text suggestions remain NULL by design "
    "(unknown ⇒ both-eligible). “Add bike greenway” reads <code>route</code>, “Add bike parking” "
    "<code>point</code>.",
    "Live cast path: POST /api/vote with a brand-new label + <code>point_type: \"route\"</code> "
    "created the vote_types row with kind <code>route</code> (then cleaned up).",
    "<code>/api/maps/nyc-bikes</code> serves voteTypes with pointType and the new object-shaped "
    "searchVoteTypes.",
    "Prod note: the same startup hook repairs the prod DB on next deploy — no manual migration. "
    "(Remember the standing rule: pg_dump backup before deploying.)",
]

CHECKLIST = [
    "Open <a href='http://localhost:3000/m/nyc-bikes'>http://localhost:3000/m/nyc-bikes</a> — no "
    "square PBTP pin should carry a route-flavored label (“Add bike greenway”, “Add bike lane”, …); "
    "those should only ever appear as diamonds.",
    "Zoom to a dense vote area: no two square pins should sit on the same street block, and two "
    "same-type squares should never be closer than ~6 city blocks.",
    "Check the longest diamond corridor on nyc-bikes / nyc-walkways: hovering should highlight a "
    "corridor of roughly ≤ 1–2.5 km (scaled to its votes), not a multi-mile snake.",
    "In point mode, type a brand-new suggestion and vote it; then check "
    "<code>curl -s 'localhost:5001/api/maps/&lt;slug&gt;' | jq .searchVoteTypes</code> — the new "
    "label should carry <code>\"pointType\": \"point\"</code>.",
    "In the vote-type selector while a ROUTE is drawn, search for a custom type that was suggested "
    "as a point — it should not appear (and vice versa).",
    "On the e-bikes map (station network) confirm top-proposal pins still appear for all vote types.",
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


SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "server/database.py": {
        "on": ["Flask API"],
        "module": ("Persistence · database.py", "all Postgres SQL — schema, votes, maps, vote-type registry"),
        "file": ("database.py", "~1100 LOC — schema init, vote CRUD, maps/lists, aggregates"),
        "outline": [
            ("init_db", "vote_types gains point_type TEXT (route|point|NULL)", True),
            ("edge_votes schema / migrations", "unchanged", False),
            ("fetch_voted_vote_type_labels", "returns {label, pointType} dicts (was bare labels)", True),
            ("normalize_point_type / get_or_create_vote_type_id", "NEW normalizer; insert stamps kind, COALESCE fills NULLs only", True),
            ("seed_presets", "preset labels force-stamp their kind (authoritative repair)", True),
            ("backfill_vote_type_kinds", "NEW — label-match repair from every authored list, idempotent", True),
            ("maps / promote / aggregates", "unchanged", False),
        ],
        "blocks": [
            "init_db — ALTER TABLE vote_types ADD COLUMN point_type",
            "fetch_voted_vote_type_labels — SELECT label, point_type",
            "normalize_point_type + get_or_create_vote_type_id(label, point_type) with COALESCE upsert",
            "seed_presets — DO UPDATE SET point_type = EXCLUDED.point_type",
            "backfill_vote_type_kinds — authored lists → UPDATE … WHERE point_type IS NULL",
        ],
    },
    "server/app.py": {
        "on": ["Flask API"],
        "module": ("API · app.py", "route handlers — maps config, vote cast, admin"),
        "file": ("app.py", "~1900 LOC — this diff touches startup, /api/vote, /api/maps"),
        "outline": [
            ("startup", "backfill_vote_type_kinds() after seed_presets()", True),
            ("_map_response", "searchVoteTypes now {label, pointType} objects", True),
            ("map_create", "custom_vote_types must declare a valid pointType (400); kinds registered at creation", True),
            ("cast_vote", "reads point_type from the body → get_vote_type_id(label, kind)", True),
            ("routing / websocket / admin", "unchanged", False),
        ],
        "blocks": [
            "imports + startup backfill call",
            "_map_response — object-shaped searchVoteTypes",
            "map_create — per-entry label/pointType validation + registry stamp after create",
            "cast_vote — vt_point_type = normalize_point_type(data.get('point_type'))",
        ],
    },
    "server/vote_store.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Votes · vote_store.py", "Redis vote state + the in-memory label↔id cache"),
        "file": ("vote_store.py", "~700 LOC — codec, counts, pubsub, vote-type cache"),
        "outline": [
            ("get_vote_type_id", "passes point_type through on first-use creation", True),
            ("codec / counts / pubsub", "unchanged", False),
        ],
        "blocks": ["get_vote_type_id(label, point_type=None) → database.get_or_create_vote_type_id"],
    },
    "client-react/src/themes.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Themes · themes.ts", "preset theme definitions + shared label resolvers"),
        "file": ("themes.ts", "~250 LOC — THEMES, iconForLabel, subdomain detection"),
        "outline": [
            ("THEMES / iconForLabel", "unchanged", False),
            ("pointTypeForLabel", "NEW — label → route|point|null; map list → searched customs → presets", True),
        ],
        "blocks": ["pointTypeForLabel — the single kind resolver (mirrors iconForLabel precedence)"],
    },
    "client-react/src/map/runtime.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Map runtime · map/runtime.ts", "resolved map config — the client's view of /api/maps"),
        "file": ("runtime.ts", "~245 LOC — MapConfig, slug/subdomain resolution, passcode"),
        "outline": [
            ("MapConfig.searchVoteTypes", "string[] → {label, pointType}[]", True),
            ("mapVoteTypesForPointType / resolution / passcode", "unchanged", False),
        ],
        "blocks": ["searchVoteTypes type + doc"],
    },
    "client-react/src/components/VoteTypeSelector/VoteTypeSelector.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("VoteTypeSelector", "the top-bar vote-type combobox"),
        "file": ("VoteTypeSelector.tsx", "~325 LOC — suggestions, search, custom entry"),
        "outline": [
            ("extraLabels (searched customs)", "kind-filtered like the default suggestions; station networks skip", True),
            ("dropdown / frozen chip / handlers", "unchanged", False),
        ],
        "blocks": ["extraLabels — filter by vt.pointType (null shows in both modes)"],
    },
    "client-react/src/utils/castVote.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Vote cast · castVote.ts", "the single client cast path (block-scoped clear-then-cast)"),
        "file": ("castVote.ts", "~265 LOC — planBlockVote, optimistic apply, POST"),
        "outline": [
            ("castVotes params", "optional pointType — the selection's kind rides the POST", True),
            ("plan / optimistic / rollback", "unchanged", False),
        ],
        "blocks": ["params.pointType + body.point_type"],
    },
    "client-react/src/context/RouteContext.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("RouteContext", "selection state + the top-bar cast"),
        "file": ("RouteContext.tsx", "~1700 LOC — one call-site change"),
        "outline": [
            ("castVote", "passes the selection's pointType to castVotes", True),
            ("everything else", "unchanged", False),
        ],
        "blocks": ["castVotes({ …, pointType }) + dep"],
    },
    "client-react/src/components/GraphLayer/topProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer · PBTPs", "pure point-based top-proposal selection"),
        "file": ("topProposals.ts", "~330 LOC — the five-step selection pipeline"),
        "outline": [
            ("computeVoteTypeWinners", "kindOf filter — route-kind labels excluded", True),
            ("dedupeWinnersByEdge", "unchanged", False),
            ("dedupeWinnersByBlock", "NEW — one pin per block across ALL types", True),
            ("spaceOutWinners", "unchanged mechanics; now step 4, same-type only by design", False),
            ("constants + selectTopProposals", "spacing 300→600 m; wires kindOf + block step", True),
        ],
        "blocks": [
            "VoteTypeKindResolver type + step-1 route-kind skip",
            "EdgeBlockKey + dedupeWinnersByBlock (singleton keys pass through)",
            "TOP_PROPOSAL_MIN_SPACING_M = 600 + rationale",
            "selectTopProposals — five-step wiring (blockKeyOf resolver)",
        ],
    },
    "client-react/src/components/GraphLayer/topProposals.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer · PBTPs", "selection tests"),
        "file": ("topProposals.test.ts", "34 tests — +8 for kinds + block dedupe"),
        "outline": [
            ("existing steps", "unchanged (600 m default still satisfies the 110 m/1.1 km fixtures)", False),
            ("kind filter / dedupeWinnersByBlock / full path", "NEW describes", True),
        ],
        "blocks": ["route-kind exclusion + unknown-kind eligibility", "cross-type block collapse + salted tie + singleton degrade", "full-path block uniqueness with edgeBlockId"],
    },
    "client-react/src/components/GraphLayer/routeProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer · RBTPs", "pure route-based top-proposal logic (clustering, corridors, anchors)"),
        "file": ("routeProposals.ts", "~880 LOC — computeRouteProposals + corridor helpers"),
        "outline": [
            ("parse / shape / coverage / corridor geometry", "unchanged", False),
            ("length-budget constants + routeLengthBudgetM", "NEW — min(2500, 600 + 150·√score)", True),
            ("RouteProposalOptions", "gains kindOf + maxRouteLengthM", True),
            ("greedyHeaviestPath", "splice fix — no more duplicated seed node in nodes[]", True),
            ("capPathToLengthBudget", "NEW — max-weight sliding window under the budget", True),
            ("computeRouteProposals", "point-kind skip; trim before the activity gates", True),
        ],
        "blocks": [
            "ROUTE_LENGTH_* constants + routeLengthBudgetM",
            "RouteProposalOptions.kindOf / maxRouteLengthM",
            "greedyHeaviestPath — leftNodes slice(0,-2) alignment fix",
            "capPathToLengthBudget — window scan, tie rules, single-edge floor",
            "computeRouteProposals — kind gate + cap wiring",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer · RBTPs", "pure-logic tests"),
        "file": ("routeProposals.test.ts", "53 tests — +10 for budget/cap/kind"),
        "outline": [
            ("existing clustering/corridor tests", "unchanged", False),
            ("routeLengthBudgetM / capPathToLengthBudget", "NEW — growth, clamp, window, ties, floor", True),
            ("length cap + kind filter through computeRouteProposals", "NEW — trimmed anchors asserted", True),
        ],
        "blocks": ["budget growth/clamp", "window trimming unit cases", "12-edge chain capped to its 4-edge hot core (anchors [4,8])", "point-kind skip / unknown-kind keep"],
    },
    "client-react/src/components/GraphLayer/graphTopology.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer · topology", "typed-array graph + CSR indexes + block keys"),
        "file": ("graphTopology.ts", "~340 LOC — one new helper"),
        "outline": [
            ("decode / adjacency / block index", "unchanged", False),
            ("edgeLengthMeters", "NEW — equirectangular edge length, shared", True),
        ],
        "blocks": ["edgeLengthMeters"],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer", "topology + votes + proposal indicators"),
        "file": ("GraphLayer.tsx", "~4000 LOC — resolver + two call sites"),
        "outline": [
            ("voteTypeKindOf", "NEW — pointTypeForLabel over the active map config", True),
            ("refreshGraphDisplay", "passes spacing + kindOf (station networks: no resolver)", True),
            ("recomputeRouteProposals", "passes kindOf to the corridor extraction", True),
            ("everything else", "unchanged", False),
        ],
        "blocks": ["voteTypeKindOf useCallback", "selectTopProposals(…, TOP_PROPOSAL_MIN_SPACING_M, kindOf)", "computeRouteProposals(…, {kindOf})"],
    },
    "docs/three-layer-model.md": {
        "on": ["React / Leaflet client", "Flask API"],
        "module": ("Docs", "the three-layer model reference"),
        "file": ("three-layer-model.md", "PBTP/RBTP terminology + §3.2 pipeline"),
        "outline": [
            ("Terminology box", "kind split + block uniqueness + 600 m spacing documented", True),
            ("§3.2 pipeline", "length budget inserted as step 4 (gates renumbered)", True),
            ("§4 vote semantics", "unchanged", False),
        ],
        "blocks": ["Kind-split paragraph", "PBTP bullet — one pin per block", "§3.2 step 4 — length budget"],
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
        <details open>
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
  @media (max-width: 680px) {{ h1 {{ font-size: 27px; }} }}
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
  .ctx-tier {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; padding: 4px 0; position: relative; }}
  .ctx-mod {{ padding-left: 18px; }} .ctx-file {{ padding-left: 36px; }}
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
    <div class="dateline">{DATE} · branch <code>fix/unify-voting</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">Top proposals now respect what a vote type IS. Every vote type carries a
  <strong>route/point kind</strong> end-to-end — declared where it's authored, recorded by the cast
  that creates a free-text suggestion, stored in <code>vote_types.point_type</code>, served in the map
  config, and repaired on startup for legacy rows — so route-flavored types (“Add bike greenway”)
  can no longer masquerade as square point pins. Point pins additionally obey the block grain
  (<strong>one pin per street block</strong>, across all types) and same-type pins spread to
  <strong>600&nbsp;m</strong>. Route corridors get a <strong>support-scaled length budget</strong>
  (600&nbsp;+&nbsp;150·√score, ≤&nbsp;2.5&nbsp;km): a peeled path is trimmed to its hottest
  contiguous stretch, so a chain of stray net-1 edges can't stretch a corridor across the borough.</p>

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
    <p style="color:var(--muted);font-size:14px;margin-top:0;">Green is added, red removed.</p>
    {''.join(diff_blocks)}
  </section>

  <footer>
    Generated from <code>changelog/changes-top-proposal-kinds.diff</code> by <code>changelog/build_top_proposal_kinds_report.py</code>.
    Regenerate with <code>git diff HEAD &gt; changelog/changes-top-proposal-kinds.diff &amp;&amp; python changelog/build_top_proposal_kinds_report.py</code>.
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
