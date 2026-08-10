#!/usr/bin/env python3
"""Generate the HTML changelog report from the captured unified diff.

Run from repo root: python changelog/build_vote_type_filter_report.py
Reads changelog/changes-vote-type-filter.diff, writes changelog/2026-08-09-vote-type-filter.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-vote-type-filter.diff")
OUT_PATH = os.path.join(HERE, "2026-08-09-vote-type-filter.html")

DATE = "2026-08-09"
TITLE = "The vote-type dropdown becomes the map's legend"


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
        "id": "legend",
        "tag": "THE PROBLEM",
        "title": "The map had icons but no key",
        "symptom": "Every top-proposal pin on the map carries a vote-type icon, and every block's heat is the differential of <em>some</em> vote type — but nothing on screen said which types existed, how much support each had, or let you look at one without the others. On <code>nyc-proposals</code> that is 17 types stacked into one indivisible picture.",
        "cause": [
            "The only per-type list in the app was the <strong>vote-type dropdown</strong> — and it was a cast-target picker, opened to answer “what am I voting for”, not “what am I looking at”",
            "It listed only the types castable in the <em>current</em> selection mode, so half the map's proposals were never named anywhere",
            "The topbar legend named the tools (start · selection · end) and one aggregate <em>Votes</em> swatch — nothing about the vote types the swatch was actually made of",
            "The two lists were the same list, drawn from the same icons, sitting 400px apart",
        ],
        "fixes": [
            "The dropdown <strong>becomes</strong> the legend: one panel, every vote type on the map, with its icon, its name and its live net support",
            "Each row carries the two things you can do with a legend entry — a <strong>checkbox</strong> (show/hide it on the map) and the <strong>row itself</strong> (make it what you're voting for)",
            "A hover-revealed <code>only</code> on each row isolates that type; the header carries <code>N/M</code> and an All / None toggle",
            "Rows whose kind doesn't match the current selection stay listed and toggleable under a divider — their proposals are on screen, so hiding them has to be possible",
        ],
        "files": [
            "client-react/src/components/VoteTypeSelector/VoteTypeSelector.tsx",
            "client-react/src/components/VoteTypeSelector/VoteTypeSelector.css",
        ],
    },
    {
        "id": "stores",
        "tag": "STATE",
        "title": "Hidden labels, not visible ones",
        "symptom": "A filter needs to be readable from React (the panel) and from three hot paths that never re-render for it (the canvas heatmap, the PBTP scan, the RBTP job). And it has to survive vote types appearing while you look at the map.",
        "cause": [
            "There is no global store in this client — map config is a module singleton read through a static context, and GraphLayer works through refs precisely to stay out of React's render path",
            "Storing the <em>visible</em> set would make every newly-arriving vote type invisible by default: a custom suggestion someone casts while you watch would silently never appear",
        ],
        "fixes": [
            "<code>map/voteTypeFilter.ts</code> holds the <strong>HIDDEN</strong> labels. Empty set = show everything, and a type nobody has seen yet is visible without anyone opting it in",
            "Module-level singleton + <code>subscribe()</code>, so hot paths call <code>isVoteTypeVisible()</code> synchronously and the UI binds through <code>useSyncExternalStore</code>",
            "<code>map/voteTypeRegistry.ts</code> answers the other half — which types exist and what each is worth — merging the map's authored types, every label with live votes, and every label cast this session",
            "Scoped per map slug in <code>sessionStorage</code>: filtering is a gesture for this visit, not a durable preference that leaves someone staring at an empty map in a month",
        ],
        "files": [
            "client-react/src/map/voteTypeFilter.ts",
            "client-react/src/map/voteTypeRegistry.ts",
            "client-react/src/hooks/useVoteTypeLegend.ts",
        ],
    },
    {
        "id": "render",
        "tag": "THE RENDER PATHS",
        "title": "Four places draw vote signal; all four had to agree",
        "symptom": "“Only show these types” is not one switch. Block heat, the canvas edge heatmap, the square point pins and the diamond corridors are computed by four different pieces of code from three different arrays.",
        "cause": [
            "<strong>Block heat</strong> is <code>topProposalDiffs</code> — the max differential across a block's types, indexed by the <em>block</em> legend",
            "<strong>Canvas heat</strong> (no-block maps) reads <code>edge_votes</code>, a precomputed net across ALL types — there is no per-type array to filter",
            "<strong>PBTP pins</strong> come from <code>selectTopProposals</code>, <strong>RBTP corridors</strong> from <code>createRouteProposalJob</code> — both already carrying a label predicate, for kind",
        ],
        "fixes": [
            "<code>topProposalDiffs</code> takes an optional legend mask: rank only visible types, and go cold (0) when none of a block's types is on",
            "The untyped fallback is <strong>suppressed</strong> while filtering — a legacy block's total can't be attributed to a type, so painting it would be heat nobody asked for",
            "The canvas re-sums per-type nets into a filtered array, cached by <em>(vote revision, filter version)</em> and skipped entirely when nothing is hidden",
            "Both proposal families take an <code>isVisible</code> predicate, applied <strong>before</strong> selection rather than to the finished list — so hiding 16 of 17 types promotes the survivor's runners-up into the freed slots instead of leaving the map near-empty",
            "One subscription in GraphLayer fans a toggle out: heat repaints immediately (cheap re-derive), pins follow on an <em>urgent</em> recompute",
        ],
        "files": [
            "client-react/src/components/GraphLayer/voteApply.ts",
            "client-react/src/components/GraphLayer/topProposals.ts",
            "client-react/src/components/GraphLayer/routeProposals.ts",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
        ],
    },
    {
        "id": "cast",
        "tag": "THE GUARANTEE",
        "title": "You can never vote into a void",
        "symptom": "A visibility filter plus a vote button is a trap: hide a type, vote for it, and the map does not move. The user has no way to tell that from the vote having failed.",
        "cause": [
            "The filter is per-label and casting is per-label, but nothing connected them",
            "Separately, a brand-new custom suggestion was invisible to the picker until the <em>next</em> map-config fetch — the server reports custom types back in <code>searchVoteTypes</code>, which the client holds as a snapshot taken at page load",
        ],
        "fixes": [
            "<code>castVotes</code> now always <code>ensureVoteTypeVisible(label)</code> — casting is the strongest statement that a type matters to you, so it clears any toggle hiding it",
            "It also <code>registerVoteTypeLabel(label)</code>, putting a fresh custom type into the searchable list and the legend on the spot",
            "Both run before the POST, so the UI reacts to the press, not to the round-trip",
            "Committing a typed <em>Suggest: “…”</em> registers it too — an explicit act, unlike every string typed on the way there",
        ],
        "files": ["client-react/src/utils/castVote.ts"],
    },
    {
        "id": "reach",
        "tag": "PLACEMENT",
        "title": "A legend you can't open isn't a legend",
        "symptom": "The vote-type control lived in <code>.vote-switcher-group</code>, which was <code>hidden-reserve</code>'d (invisible, no pointer events) until you had something to vote on. As a picker that was right. As the legend it is fatal — the map's key would be unreachable until you drew on the map.",
        "cause": [
            "The gate encoded the control's old single meaning: no selection, nothing to cast, nothing to pick",
        ],
        "fixes": [
            "The gate comes off; the group is always visible. <strong>Cast −/+ and Clear are untouched</strong> — they are still the things that need a selection",
            "The prefix label goes <code>Vote:</code> → <code>Proposals:</code>, which names both jobs: the proposals on the map, and the proposal you're voting for",
            "A filtered map reads exactly like an empty one, so the collapsed control carries an accent <code>N/M</code> badge even with the panel shut",
        ],
        "files": ["client-react/src/components/TopBar/TopBar.tsx"],
    },
    {
        "id": "revision",
        "tag": "REVISION",
        "title": "Two families, two headings, one column of eyes",
        "symptom": "First pass headed the panel <em>Shown on map \u00b7 N/M \u00b7 All\u2009/\u2009None</em> and split it with a divider that renamed itself as you drew. One global count and one global All\u2009/\u2009None were the wrong grain, the divider read as an afterthought, and a checkbox is a form control \u2014 not what \u201cis this drawn on the map\u201d looks like.",
        "cause": [
            "The split was on <em>castable right now</em>, so a corridor type changed house the moment you finished drawing a route \u2014 the heading moved under the reader",
            "A single <code>N/M</code> spanning the whole panel could not say <em>which</em> half was turned down \u2014 \u201c8 of 17\u201d reads the same whether you hid the pins or the corridors",
            "One global All\u2009/\u2009None is the wrong grain: the useful gesture is \u201call the corridors\u201d or \u201call the pins\u201d, not \u201ceverything\u201d",
        ],
        "fixes": [
            "Split by proposal FAMILY instead \u2014 <strong>Point proposals</strong> and <strong>Route proposals</strong>, headings that never move. The family you can cast into leads",
            "Each heading carries its own show-all\u2009/\u2009hide-all eye, in the <em>same left column</em> as the rows' eyes, so the section control and its members read as one unit",
            "From a mixed section that eye SHOWS everything \u2014 the recovering direction; a blank map is the outcome worth making people ask for twice",
            "Every toggle is now the conventional <strong>eye / eye-with-slash</strong>. Deliberately NOT restyled into the app's square-capped house language: this control's job is instant recognition",
            "An <strong>All proposals</strong> master row sits above the families \u2014 the same control at a wider scale \u2014 and is suppressed on single-family maps, where it would only shadow the one section's own eye",
            "Each heading carries <code>(n/N shown)</code> for its own group, which is the grain that was missing: one global count said nothing actionable, while a per-family count tells you which half of the map you have turned down",
            "The collapsed field keeps a struck-through eye rather than a number \u2014 it has room for one signal, and <em>filtered</em> is the one that matters there",
            "New <code>setVoteTypesVisible</code> commits a whole section in ONE write, so a 12-type family costs one repaint and one recompute rather than twelve",
        ],
        "files": [
            "client-react/src/components/EyeIcon.tsx",
            "client-react/src/components/VoteTypeSelector/VoteTypeSelector.tsx",
            "client-react/src/components/VoteTypeSelector/VoteTypeSelector.css",
            "client-react/src/map/voteTypeFilter.ts",
        ],
    },
]


VERIFY = [
    "Driven end-to-end in the running app on <code>/m/nyc-proposals</code> (17 vote types, 3.3M edges, 287k blocks) — not just unit-tested.",
    "The filter reaches every render path, measured through <code>cityedit.dumpState()</code>: isolating <em>Add protected bike lane</em> took <code>blockHeatNonzero</code> <strong>466 → 154</strong> and <code>routeProposals</code> <strong>8 → 3</strong>; pressing <em>All</em> restored <strong>466 / 8</strong> exactly.",
    "The auto-unhide guarantee was exercised for real: with the map filtered to <strong>1/17</strong>, a hidden type was selected and <code>+</code> pressed — the badge went to <strong>2/17</strong> on the press.",
    "Point pins visibly disappeared from the map when the corridor type was isolated, and the corridor heat returned on <em>All</em>.",
    "The station-network path (<code>/m/e-bikes-3</code>, <code>network: ebikes</code>, no block layer) renders unchanged, with the panel reading <strong>1/1</strong> — that map is the one that exercises the canvas <code>visibleEdgeVotes</code> branch.",
    "<strong>391 unit tests pass</strong> (29 new): the filter store, the registry/legend composition, <code>topProposalDiffs</code> masking, and the <em>filter-then-limit</em> promotion rule.",
    "<code>npx tsc -b</code> clean — Vite dev never type-checks, so this is the only thing between an edit and a broken client build.",
    "No console errors on either map.",
    "Unfiltered maps take the <strong>same code path as before</strong> by construction: <code>legendVisibilityMask</code> returns <code>null</code> with an empty hidden set, and every consumer short-circuits on it.",
    "Staged as a <strong>partial commit</strong> — another session was mid-flight in <code>GraphLayer.tsx</code>, <code>TopBar.tsx</code> and <code>routeProposals.ts</code>; it landed its work first, and the diff was read back to confirm the staged hunks are only this workstream's.",
]


CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-proposals</code> and click <strong>Proposals:</strong> — expect a panel headed <code>SHOWN ON MAP 17/17</code>, one row per vote type with a checkbox, icon, name and support count.",
    "Uncheck two or three types. The heat should change <strong>immediately</strong>; the pins and corridors follow a beat later (they need the full edge-table scan).",
    "Hover a row and click <code>only</code> — the map should collapse to that one type, and the header should read <code>1/17</code> in the accent color.",
    "Close the panel and confirm the collapsed control still shows the accent <code>1/17</code> badge — <em>this is the thing that stops a filtered map looking like a broken one</em>.",
    "Press <strong>All</strong> and confirm the map comes back exactly as it was.",
    "Now the guarantee: hide a type, select it as your vote target, place a point and press <code>+</code>. It should <strong>re-check itself</strong> and reappear.",
    "Type a brand-new suggestion, cast it, then reopen the panel — it should be listed and searchable without a page reload.",
    "Scroll to the divider (<em>Corridor proposals · on the map, vote elsewhere</em>) and confirm those rows still toggle but don't become your cast target.",
    "Reload the page with a filter active — it should persist (sessionStorage, per map). Open the map in a <em>new</em> tab and confirm it starts unfiltered.",
    "Judgement call worth your eye: <strong>None</strong> hides everything and blanks the map. It is reversible and the badge reads <code>0/17</code>, but tell me if you'd rather it wasn't offered.",
]


SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

# label, summary, changed?
FILE_CONTEXT = {
    "client-react/src/map/voteTypeFilter.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · map runtime", "Module-level singletons the whole client reads without going through React — the resolved map config, and now which of its vote types are drawn"),
        "file": ("voteTypeFilter.ts", "~140 LOC — new. One Set, one version counter, one subscriber list"),
        "outline": [
            ("header comment", "new — names the four render paths that read this store, and why it holds HIDDEN not visible", True),
            ("ensureLoaded / persist", "new — sessionStorage, keyed by map slug, reloaded when the slug changes", True),
            ("getHiddenVoteTypes / isVoteTypeVisible", "new — the synchronous reads the hot paths use", True),
            ("getVoteTypeFilterVersion", "new — cache key for filter-derived arrays", True),
            ("setVoteTypeVisible / toggle / ensureVisible", "new — ensureVisible is the cast path's guarantee", True),
            ("showAllVoteTypes / showOnlyVoteTypes", "new — the panel's All / None / only gestures", True),
            ("legendVisibilityMask", "new — legend → Uint8Array, or NULL when nothing is hidden", True),
            ("subscribeVoteTypeFilter", "new — GraphLayer's repaint trigger and the hook's store binding", True),
        ],
        "blocks": [
            "HIDDEN, not visible: an empty set means show everything, so a vote type that appears mid-session needs nobody to opt it in",
            "legendVisibilityMask returns null when the set is empty — that null is the fast path every consumer short-circuits on, so an unfiltered map does exactly the work it did before",
            "commit() diffs before notifying, so a no-op toggle can't trigger a full proposal recompute",
            "sessionStorage not localStorage: a filter is a browsing gesture for this visit, not a preference that outlives the reason for it",
            "ensureLoaded re-reads on a slug change — the store is a singleton but maps are not",
        ],
    },
    "client-react/src/map/voteTypeRegistry.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · map runtime", "The companion store: WHICH vote types this map has, and what each is worth — the data the legend is made of"),
        "file": ("voteTypeRegistry.ts", "~160 LOC — new. Live nets, session labels, and the legend composition"),
        "outline": [
            ("header comment", "new — the three sources that merge here, and why nets are block-grain", True),
            ("publishVoteTypeNets", "new — GraphLayer's batched sweep pushes per-type totals in", True),
            ("registerVoteTypeLabel", "new — a cast or committed suggestion, searchable immediately", True),
            ("subscribe / version / reset", "new — store plumbing + a test seam", True),
            ("VoteTypeLegendEntry", "new — label, icon, kind, net, onMap, castable", True),
            ("buildVoteTypeLegend", "new — composes cfg + nets + session labels into legend order", True),
        ],
        "blocks": [
            "Three sources merge: the map's authored voteTypes (always listed), every label carrying live votes, and every label cast this session",
            "searchVoteTypes is the SERVER's load-time snapshot — it only earns a legend row once it has a live net, otherwise the legend becomes a dump of every string ever suggested",
            "Nets are signed and NOT masked by the filter: hiding a type must not zero its own row, or you could never find it again",
            "Block-grain where the map has blocks — deduped, one person one block, matching the number the proposal modal quotes",
            "castable is a partition, not a filter: a corridor type in point mode still gets a row, because its diamonds are on screen",
            "publishVoteTypeNets diffs before notifying — an unchanged sweep must not re-render the panel",
        ],
    },
    "client-react/src/hooks/useVoteTypeLegend.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · hooks", "The reactive view onto the two module-level stores, for the components that need to re-render"),
        "file": ("useVoteTypeLegend.ts", "~45 LOC — new. Two hooks over useSyncExternalStore"),
        "outline": [
            ("useHiddenVoteTypes", "new — the hidden Set, re-rendering on every toggle", True),
            ("useVoteTypeLegend", "new — version-keyed memo over buildVoteTypeLegend", True),
        ],
        "blocks": [
            "The stores are singletons so the hot paths can read them synchronously; these hooks exist only for the UI",
            "getSnapshot returns the SAME Set object between mutations — useSyncExternalStore requires referential stability or it loops",
            "The legend array is rebuilt on a version key, not on every render: the panel must not churn on every vote poll",
        ],
    },
    "client-react/src/map/voteTypeFilter.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 map runtime \u00b7 tests", "Unit coverage for the filter store"),
        "file": ("voteTypeFilter.test.ts", "~95 LOC \u2014 new. 10 tests"),
        "outline": [
            ("default = show everything", "new \u2014 including labels nobody has seen", True),
            ("hide / toggle / ensureVisible", "new \u2014 ensureVisible is the cast path's guarantee", True),
            ("showOnlyVoteTypes", "new \u2014 isolate, and hide-them-all", True),
            ("subscriber + version semantics", "new \u2014 no-ops must not notify or bump", True),
            ("legendVisibilityMask", "new \u2014 null fast path, and the 1/0 mapping", True),
        ],
        "blocks": [
            "The default-visible case is asserted for an UNKNOWN label, which is the whole reason the store holds hidden rather than visible",
            "No-op assertions matter here: a spurious notify triggers a full proposal recompute on a big map",
            "The null-mask cases pin the fast path unfiltered maps depend on",
        ],
    },
    "client-react/src/map/voteTypeRegistry.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 map runtime \u00b7 tests", "Unit coverage for the registry and the legend composition"),
        "file": ("voteTypeRegistry.test.ts", "~110 LOC \u2014 new. 10 tests"),
        "outline": [
            ("authored types always listed", "new \u2014 even at zero votes", True),
            ("castable partition + order", "new \u2014 castable first, discovery order within", True),
            ("live nets, including negatives", "new \u2014 a counter-voted type reads cold", True),
            ("unauthored label appears once voted", "new", True),
            ("search-only type with no net stays out", "new \u2014 the legend describes what is DRAWN", True),
            ("session cast listed immediately", "new", True),
            ("station networks / unknown kind", "new \u2014 castable in both modes", True),
            ("notify + register idempotence", "new", True),
        ],
        "blocks": [
            "The search-only exclusion is the subtle rule: searchVoteTypes is a load-time snapshot, and listing all of it would turn the legend into a dump of every string ever suggested",
            "Negative nets are asserted explicitly \u2014 the legend has to be able to say a type is net-against",
            "The station-network case mirrors mapVoteTypesForPointType's rule, so the two can't drift",
        ],
    },
    "client-react/src/components/GraphLayer/voteApply.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 GraphLayer \u00b7 tests", "Unit coverage for the vote-array math, including the signed block heat"),
        "file": ("voteApply.test.ts", "~300 LOC \u2014 a new describe block for the mask"),
        "outline": [
            ("applyEdgeVoteChange / authoritative / block counts", "unchanged", False),
            ("topProposalDiffs \u2014 existing cases", "unchanged \u2014 all still pass with the mask defaulted", False),
            ("topProposalDiffs \u2014 legend visibility mask", "new \u2014 5 tests", True),
        ],
        "blocks": [
            "Ranks only visible types \u2014 the block's actual top proposal is hidden and the runner-up takes over",
            "All-hidden goes cold (0), and both arm ceilings go to 0 with it",
            "The untyped fallback is asserted SUPPRESSED under a mask \u2014 the non-obvious half of the rule",
            "An index past the mask stays visible: that's an optimistically-appended type, and nothing hid it",
            "A visible net-against type still reports negative, so cold heat survives filtering",
        ],
    },
    "client-react/src/components/GraphLayer/topProposals.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 GraphLayer \u00b7 tests", "Unit coverage for PBTP selection \u2014 the five-step winner pipeline"),
        "file": ("topProposals.test.ts", "~430 LOC \u2014 a new describe block for the visibility predicate"),
        "outline": [
            ("winners / dedupe / spacing / limit / full path", "unchanged", False),
            ("route/point kind filter", "unchanged", False),
            ("legend visibility filter", "new \u2014 3 tests", True),
        ],
        "blocks": [
            "Drops hidden types, and admits everything when no resolver is passed \u2014 the before/after contract",
            "The third test is the important one: filter THEN limit, so a hidden type's slot is taken by the survivor's runner-up rather than left empty",
            "That test would pass either way if the predicate were applied to the finished list \u2014 it is written against counts (9, 8) specifically to catch that",
        ],
    },
    "client-react/src/components/EyeIcon.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 shared icons", "Sibling of CheckIcon: the small glyphs shared across components rather than owned by one"),
        "file": ("EyeIcon.tsx", "~50 LOC \u2014 new. One component, two states"),
        "outline": [
            ("header comment", "new \u2014 records WHY this one glyph is not in the house style", True),
            ("open eye", "new \u2014 the conventional lens + pupil", True),
            ("eye-with-slash", "new \u2014 the conventional off state", True),
        ],
        "blocks": [
            "The one glyph in the app deliberately NOT drawn to the square-cap/miter house language \u2014 recognition beats consistency for a control whose whole job is to be understood instantly",
            "Standard geometry, standard round caps: a restyled eye is a shape people have to decode, and this one sits 14px tall in a dense list",
            "`off` swaps the path rather than overlaying a slash on the open eye \u2014 stacking both muddies at this size",
            "aria-hidden: the accessible name lives on the surrounding button, never doubled here",
        ],
    },
    "client-react/src/components/VoteTypeSelector/VoteTypeSelector.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client · VoteTypeSelector", "The topbar control that was the cast-target picker and is now also the map's legend"),
        "file": ("VoteTypeSelector.tsx", "~400 LOC — the dropdown becomes a legend panel; the combobox behavior is preserved"),
        "outline": [
            ("header comment", "new — one list, two jobs, and why", True),
            ("PanelRow / formatNet", "new — the row shape and its signed count", True),
            ("rows (legend + theme fallback)", "CHANGED — was mapVoteTypesForPointType, now the full legend", True),
            ("searchOnlyRows", "CHANGED — search-only customs, now excluding what the legend already lists", True),
            ("frozenLabel / allowCustom", "unchanged behavior, re-derived from the new rows", False),
            ("shownCount / totalCount / isFiltered", "new — measured over the WHOLE legend, not the query", True),
            ("keyboard nav + selectOption", "CHANGED — counts rows + the custom option; registers custom labels", True),
            ("swallow()", "new — mousedown guard for the checkbox / only buttons", True),
            ("the collapsed control", "+ the accent N/M badge", True),
            ("the panel: head · rows · divider · suggest · empty", "new — the legend itself", True),
        ],
        "blocks": [
            "Two affordances per row, deliberately distinct: a checkbox on the LEFT for visibility, an accent left-rule for the cast target — two checkmarks would have been unreadable",
            "The gestures fire on mousedown and swallow the event: the row's own mousedown would otherwise commit the type and close the panel",
            "shownCount is measured over every row, not the filtered view — the readout is a statement about the MAP, not about the query",
            "Non-castable rows keep their eye and lose their click target \u2014 their pins are on screen either way, and hiding them is the whole point",
            "The struck-through eye on the collapsed control is the whole safety net: a filtered map is otherwise indistinguishable from an empty one",
            "The theme-preset fallback is preserved for maps that authored no vote types of their own",
        ],
    },
    "client-react/src/components/VoteTypeSelector/VoteTypeSelector.css": {
        "on": ["React / Leaflet client"],
        "module": ("Client · VoteTypeSelector", "The control's stylesheet — the fixed selection box, and now the legend panel"),
        "file": ("VoteTypeSelector.css", "~480 lines — the panel section is new; the control itself is untouched"),
        "outline": [
            (".vote-type-selector / -control / -input", "unchanged", False),
            (".vote-type-dropdown", "CHANGED — gains max-height + scroll", True),
            (".vt-panel-head", "new — sticky, opaque, with the N/M readout", True),
            (".vt-section", "new — the castable / not-castable divider", True),
            (".vote-type-option states", "CHANGED — is-casting / is-hidden / is-uncastable", True),
            (".vt-vis / .vt-vis-box", "new — the visibility checkbox + its 110ms tick", True),
            (".vt-tail / .vt-net / .vt-only", "new — one right-hand slot, count ↔ only", True),
            (".vote-type-filter-badge", "new — the collapsed-control badge", True),
            ("breakpoints", "unchanged", False),
        ],
        "blocks": [
            "Every new rule is built from the app's existing tokens — zero radius, hairlines, --accent, --font-size-2xs — so the panel reads as the same object as the rest of the bar",
            "The heading's left padding equals a row's border + inset, which is what puts the section eye and the row eyes in one unbroken column",
            "The count and the `only` button share one grid cell and crossfade — the column width is fixed, so rows never reflow under the cursor",
            "@media (hover: none) pins the count visible on touch, where there is no hover to reveal `only` behind",
            "Hidden rows are struck through and dimmed to 0.38, not removed — you have to be able to find one again",
            "max-height + scroll: the legend lists every type on the map, which is 17+ on curated maps",
        ],
    },
    "client-react/src/components/GraphLayer/voteApply.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · GraphLayer", "Pure vote-array math: applying casts and deltas, and deriving the per-block heat value"),
        "file": ("voteApply.ts", "~300 LOC — one function gains an optional mask"),
        "outline": [
            ("legendIndex / rederiveNodes", "unchanged", False),
            ("applyMyVoteChange / applyEdgeVoteChange", "unchanged", False),
            ("topProposalDiffs", "CHANGED — optional legend-visibility mask", True),
            ("applyBlockCounts / applyAuthoritativeCounts", "unchanged", False),
        ],
        "blocks": [
            "The mask is optional and defaults to null — the unfiltered call is byte-for-byte the old behavior",
            "With a mask, a block ranks only its visible types and goes cold (0) when none of them is on",
            "The untyped fallback is SUPPRESSED while filtering: a legacy block's total can't be attributed to a type, so painting it would be heat the user didn't ask for",
            "A legend index past the end of the mask stays VISIBLE — that's a type appended by an optimistic cast, which nothing has hidden",
            "Negative differentials survive masking, so a visible net-against type still paints on the cold arm",
        ],
    },
    "client-react/src/components/GraphLayer/topProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · GraphLayer", "PBTP selection — which single hot edges earn a square top-proposal pin"),
        "file": ("topProposals.ts", "~380 LOC — a second label predicate alongside the kind one"),
        "outline": [
            ("VoteTypeKindResolver", "unchanged", False),
            ("VoteTypeVisibility", "new — the legend-toggle predicate type", True),
            ("computeVoteTypeWinners", "CHANGED — skips hidden labels in step 1", True),
            ("dedupe / spacing / limit steps", "unchanged", False),
            ("selectTopProposals", "CHANGED — threads isVisible through", True),
        ],
        "blocks": [
            "Applied in STEP 1, before dedupe/spacing/limit — not to the finished list",
            "That ordering is the whole point: hiding types frees ranked slots, so the survivors' runners-up get promoted instead of the map going near-empty",
            "It sits beside the kind filter because they are the same shape — a label predicate over the legend — but they are different questions and stay separate parameters",
            "Both are optional; omitted, every type is admitted exactly as before",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · GraphLayer", "RBTP computation — clustering the vote graph into hot corridors, as a sliced job"),
        "file": ("routeProposals.ts", "~1,300 LOC — one option, applied where the job picks its types"),
        "outline": [
            ("parse / shape / coverage helpers", "unchanged", False),
            ("RouteProposalOptions", "+ isVisible", True),
            ("createRouteProposalJob type selection", "CHANGED — skips hidden types before clustering", True),
            ("step / finish / computeRouteProposals", "unchanged", False),
        ],
        "blocks": [
            "Filtered at the type-selection loop, so a hidden type's clustering never runs at all — a filtered map recomputes FASTER, not slower",
            "Same slot as the existing kind filter, two lines apart, for the same reason",
            "The job's determinism contract is untouched: skipping a type removes a slice, it doesn't reorder anything",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client · GraphLayer", "The Leaflet layer that owns topology, vote data, the canvas heatmap, both proposal families and every marker on the map"),
        "file": ("GraphLayer.tsx", "~5,100 LOC — this change wires the filter into four render paths and publishes the legend's counts"),
        "outline": [
            ("imports", "+ voteTypeFilter, voteTypeRegistry", True),
            ("maxVotesFilterRef", "new — the heat scale must rescale on a toggle too", True),
            ("recomputeTopProposals", "+ isVoteTypeVisible", True),
            ("broadcastBlockVotes", "+ legendVisibilityMask(block_vote_type_legend)", True),
            ("recomputeRouteProposals", "+ isVisible", True),
            ("publishVoteTypeSupport", "new — per-type nets → the registry, in the batched sweep", True),
            ("recomputeAllProposals", "+ publishes the counts", True),
            ("filter subscription", "new — heat now, pins on an urgent recompute", True),
            ("visibleEdgeVotes", "new — filtered per-edge nets for the canvas path", True),
            ("redraw (canvas heatmap)", "CHANGED — reads visibleEdgeVotes; scale keyed on the filter too", True),
            ("hover / selection / markers / modal", "unchanged — all derive from the filtered arrays", False),
        ],
        "blocks": [
            "One subscription fans a toggle to all four paths: heat repaints on the spot, proposals follow on an URGENT recompute (the 150ms idle timeout, not the 4s background one) — the user is watching for them",
            "Block heat is a cheap re-derive from data already in memory, which is what makes the checkbox feel instant while the edge-table scan catches up",
            "visibleEdgeVotes only ever runs on maps WITHOUT a block layer (block maps return before it) — those are the station networks, so the O(nEdges) pass is cheap; it is cached by (rev, filter version) and skipped when nothing is hidden",
            "maxVotesFilterRef exists because hiding the map's busiest type must RESCALE the ramp — otherwise everything left reads at the bottom of the old scale",
            "publishVoteTypeSupport prefers block grain and is NOT masked — the legend must keep reporting a hidden type's support",
            "It rides the existing batched sweep, so it costs one extra pass over an array that sweep already walks",
        ],
    },
    "client-react/src/utils/castVote.ts": {
        "on": ["React / Leaflet client", "Flask API"],
        "module": ("Client · vote casting", "Plans a block-semantics vote, applies it optimistically, POSTs it, and reconciles the server's authoritative answer"),
        "file": ("castVote.ts", "~290 LOC — two calls at the top of castVotes"),
        "outline": [
            ("planBlockVote / voteButtonState", "unchanged", False),
            ("castVotes: register + ensure visible", "new — before the plan, before the POST", True),
            ("optimistic apply / POST / capped / evicted / cleared", "unchanged", False),
        ],
        "blocks": [
            "ensureVoteTypeVisible closes the trap: without it you could hide a type, vote for it, and watch the map not move",
            "registerVoteTypeLabel makes a brand-new custom label searchable and legend-listed on the press, rather than after the next map-config fetch",
            "Both run BEFORE the request, so the UI reacts to the press, not to the round-trip — and both are harmless if the cast then fails",
            "Placed at the top of castVotes, so every entry point (topbar, proposal modal, route card) gets the guarantee for free",
        ],
    },
    "client-react/src/components/TopBar/TopBar.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client · TopBar", "The map header: logo, the start/end legend, the Map + Proposals selectors, the cast controls and the nav rail"),
        "file": ("TopBar.tsx", "~270 LOC — one gate removed, one label changed"),
        "outline": [
            ("logo / legend / mode switcher / NavRail", "unchanged", False),
            ("vote-switcher-group hidden-reserve gate", "REMOVED — the legend can't be gated on having a selection", True),
            ("prefix label Vote: → Proposals:", "CHANGED — names both of the control's jobs", True),
            ("actions-group (calculating / cast / clear)", "unchanged — these DO still need a selection", False),
        ],
        "blocks": [
            "hidden-reserve is visibility:hidden + pointer-events:none, so the cell always occupied its space — removing it changes no layout, only reachability",
            "The gate was right when the control only picked a cast target; it is fatal now that the control is also the map's key",
            "Cast −/+ and Clear keep their gates — the distinction is now meaningful rather than incidental",
            "One touched line each, deliberately: another session was rewriting this file's right-hand column at the same time",
        ],
    },
}


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

  <p class="lede">The map drew 17 vote types at once and offered no way to name them, weigh them, or look at one without the others — the only per-type list in the app was a <em>cast-target picker</em> that hid half of them and vanished until you had a selection. So the dropdown becomes the <strong>legend</strong>: every type on the map, with its icon, its name, its live net support, a checkbox to show or hide it, and a hover <code>only</code> to isolate it. Toggling reaches all four things that draw vote signal — block heat, the canvas heatmap, the square point pins and the diamond corridors — and is applied <em>before</em> selection, so hiding sixteen of seventeen types promotes the survivor’s runners-up rather than emptying the map. Casting always un-hides the type you voted for, because a filter plus a vote button is otherwise a trap.</p>

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
    Generated from <code>changelog/changes-vote-type-filter.diff</code> by <code>changelog/build_vote_type_filter_report.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes-vote-type-filter.diff &amp;&amp; python changelog/build_vote_type_filter_report.py</code>.
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
