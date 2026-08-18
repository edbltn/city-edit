#!/usr/bin/env python3
"""Generate the HTML changelog report from the captured unified diff.

Run from repo root: python changelog/build_report.py
Reads changelog/changes-optimistic-vote.diff, writes changelog/2026-08-18-optimistic-vote.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-optimistic-vote.diff")
OUT_PATH = os.path.join(HERE, "2026-08-18-optimistic-vote.html")

DATE = "2026-08-18"
TITLE = "Voting that answers in the click’s own tick"


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
        "id": "measure",
        "tag": "MEASUREMENT",
        "title": "Where the second actually went",
        "symptom": "Pressing <code>−</code> or <code>+</code> took about a second to show anything.",
        "cause": [
            "<strong>Not the request.</strong> <code>perf/vcast.mjs</code> (a sibling of <code>vtoggle.mjs</code>) records the mousedown, the synchronous handler, the button's cast-state class, every <code>block-votes</code> broadcast that actually moves a block, the first WebGL draw after it, the POST and the WebSocket frame. On localhost the POST resolved in 19–25ms and the synchronous handler returned in 1–4ms. Neither was ever the wait",
            "<strong>Cause 1 — the map waited for its own echo.</strong> <code>castVotes</code> was already optimistic, but only over EDGE counts. On a map with a block layer the polygons ARE the heat and edges show only on hover/selection, so the optimistic apply moved a number nothing on screen reads. Every measured heat change carried <code>source: \"delta\"</code>: the first visible change was the server's broadcast coming back — 53–105ms on localhost, and a full round trip wherever the user actually is",
            "<strong>Cause 2 — a recompute the cast forced.</strong> <code>scheduleOwnCastRecompute</code> fires 400ms after every press and it re-clustered EVERY vote type: <code>20 corridors in 1215.0ms (9/9 types clustered over 9 slices)</code>. 1110–1126ms of long tasks, landing right on top of the press. This is the one the brief predicted no amount of optimistic rendering would fix, and it is correct — it froze the thread whatever the heat was doing",
            "<strong>Not the two remaining candidates.</strong> The red/blue cast highlight was already driven by the synchronous store write (22–28ms, a React commit behind the handler) and needed no change; and there is no re-render sweep over the vote store on the vote path",
        ],
        "fixes": [
            "Both causes fixed below. The harness stays in the tree (<code>perf/vcast.mjs</code>) so the numbers are re-derivable, alongside <code>perf/vcast-verify.mjs</code>, which checks the second fix's correctness rather than its speed",
        ],
        "files": ["perf/vcast.mjs", "perf/vcast-verify.mjs"],
    },
    {
        "id": "predict",
        "tag": "OPTIMISTIC APPLY",
        "title": "Predict the quantity the UI actually paints",
        "symptom": "A vote lives in three Redis layers — the edge aggregate, the per-block identity hashes, and the deduped block aggregate. The client optimistically moved the first. The heatmap reads the third.",
        "cause": [
            "<code>applyMyVoteChange</code> bumped <code>edge_votes</code> / <code>edge_vote_types</code>. The block layer (<code>block_votes</code> / <code>block_vote_types</code>, which <code>topProposalDiffs</code> turns into every polygon's colour) only ever moved in <code>applyBlockCounts</code>, on the authoritative delta",
            "The block number is NOT the edge count, and it is NOT their sum: a block counts a person ONCE per (vote type, direction) however many of its edges carry their vote — server-side that is <code>HLEN</code> of the <code>bd:&lt;slug&gt;:&lt;mode&gt;:&lt;block&gt;:&lt;vt&gt;:&lt;dir&gt;</code> identity hash, mirrored into <code>bagg:</code> only on the voter's presence boundary",
        ],
        "fixes": [
            "<code>blockVoteDeltas</code> predicts exactly that boundary: for each touched block, do I hold an up / a down anywhere in it BEFORE the plan, and do I AFTER. The move is the difference of two booleans per arm, so a second press on the same corridor is <strong>0</strong> rather than +1, a flip is <strong>(+1 down, −1 up)</strong> rather than +1 alone, and extending a vote onto more edges of a block I already hold is <strong>0</strong>",
            "<code>planBlockVote</code> computes it in the same pass it already used to derive coverage, so a press costs one walk of its touched blocks rather than three",
            "<code>applyMyBlockVoteChange</code> is the increment twin of <code>applyBlockCounts</code>' SET — same bookkeeping (<code>block_votes</code> stays total deduped activity, the breakdown stays sorted by total desc), so the confirming delta lands as a literal no-op",
            "One event per press, not one per transition group: a clear on edge A and a cast on edge B in the same block cancel out, so splitting the prediction would make each half wrong",
            "Known blind spot, documented in the code: the server dedupes by COUNTING identity (an IP hash), not by device, so a second device behind one IP that already holds the block makes the real move 0 where this predicts ±1. Rare, and the idempotent SET corrects it",
        ],
        "files": [
            "client-react/src/utils/castVote.ts",
            "client-react/src/utils/blockSelection.ts",
            "client-react/src/components/GraphLayer/voteApply.ts",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
        ],
    },
    {
        "id": "survive",
        "tag": "RECONCILIATION",
        "title": "An optimistic write has to survive until its own echo",
        "symptom": "Two background refreshes would happily install a server snapshot over a vote that had not reached the server yet — the vote pops off and then reappears, which reads as a bug rather than as lag.",
        "cause": [
            "<code>/api/my-votes</code> → <code>resetMapVotes</code> deletes every local row the response does not confirm (deliberately — a stale localStorage silently turns the next cast into a block-unvote). A response READ before the cast was persisted knows nothing about it. The existing <code>versionAtFetch</code> guard only catches a cast that mutates the store DURING the fetch, not one that happened just before it",
            "<code>/api/graph-votes</code> → <code>fetchVotes</code> replaces <code>graphDataRef</code> wholesale and replays only deltas with <code>rev &gt; bodyRev</code>. An optimistic write has no rev, so it was simply discarded",
            "Neither path could tell a speculative row from a settled one — in the store they are the same row",
        ],
        "fixes": [
            "<code>utils/pendingVotes.ts</code> makes the distinction explicit: a cast registers before the request goes out and leaves the ledger when its own delta arrives (matched on mode + vote type + an edge in common), when it fails and rolls back, or on a 20s backstop so a dropped socket cannot shield a stale entry forever",
            "<code>resetMapVotes</code> applies server truth first and then replays what is still in flight, logging <code>restoring N in-flight</code>; <code>reconcileEdge</code> skips any (edge, label) a pending cast holds",
            "<code>fetchVotes</code> re-applies pending casts on top of the installed snapshot, and the block-heat broadcast moves AFTER the replay so what is painted includes them",
            "Double-counting is not a risk: if the body already contains the vote, the confirming delta's idempotent SET corrects it within the same second",
        ],
        "files": [
            "client-react/src/utils/pendingVotes.ts",
            "client-react/src/utils/voteStore.ts",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
        ],
    },
    {
        "id": "rollback",
        "tag": "ROLLBACK",
        "title": "A silent revert is indistinguishable from a bug",
        "symptom": "A failed cast rolled the optimistic apply back and said nothing.",
        "cause": [
            "The catch block reversed the transitions but only raised a toast when the SERVER had sent a reason — so offline, a 500, or a timeout reverted in silence",
            "Rollback also read the transitions it had captured at press time, which is wrong if a LATER press on the same control has since rewritten the same edges",
        ],
        "fixes": [
            "Every rollback now speaks, through the toast App.tsx already listens for (<code>vote-rejected</code> → <code>ErrorToast</code>) — the same strip as &quot;Route not found&quot; and the server's own refusals. It fires once: the passcode gate and a server-supplied reason suppress the generic message rather than doubling it",
            "The block half of the rollback is RECOMPUTED against the store as it stands, not produced by negating the forward prediction — a rollback can land after other writes have moved the same blocks, and a negated stale prediction would be a second wrong number rather than a correction",
            "<strong>A rollback cannot race a subsequent click.</strong> A press that failed after a later press on the same (mode, vote type) is superseded: it settles its ledger entry, logs, and does NOT restore what it saw. Undoing there would revert the user's newest intent; the newer press's own response is what reconciles the server's view",
            "The partial-decline paths (<code>capped</code>, <code>evicted</code>) go through the same recomputed revert, and a capped edge stops shielding — the server never took it, so a refresh that omits it is telling the truth",
        ],
        "files": ["client-react/src/utils/castVote.ts"],
    },
    {
        "id": "corridors",
        "tag": "PERF",
        "title": "A cast invalidates one vote type, not twelve",
        "symptom": "Every press was followed by ~1.1s of long tasks: <code>20 corridors in 1215.0ms (9/9 types clustered over 9 slices)</code>.",
        "cause": [
            "<code>voteEpochRef</code> is bumped by every mutation of in-memory vote state, and <code>routeCacheRef</code> dropped its whole per-type cluster cache whenever the epoch moved",
            "But a corridor for one vote type reads only that type's nets and the topology — the exact insight <code>e3086cc</code> built the per-type cache on to make a legend toggle paint-only. A cast changes ONE label's nets, so the other eleven types' corridors were re-derived from scratch for no reason",
        ],
        "fixes": [
            "The epoch now carries WHICH labels moved: <code>bumpVoteEpoch([label])</code> from the optimistic press and from each applied delta, <code>bumpVoteEpoch()</code> (meaning &quot;everything&quot;) from a whole-snapshot install or a mode switch",
            "<code>recomputeRouteProposals</code> drops only the dirty labels' cached clusters. A label the enumeration never judged (a brand-new suggestion, detected via <code>eligibleLegendLen</code> rather than by scanning the list) still forces a rebuild; a label it judged and ruled out — a point-kind type — costs a no-op delete rather than a full re-enumeration",
            "Verified for CORRECTNESS, not just speed: <code>perf/vcast-verify.mjs</code> presses a vote in one session and compares the incrementally-recomputed corridor list against what a cold load's full 9-type recompute produces in the same vote state. Identical",
        ],
        "files": ["client-react/src/components/GraphLayer/GraphLayer.tsx", "perf/vcast-verify.mjs"],
    },
]


VERIFY = [
    "<strong>Measured before and after on the same corridor</strong> (nyc-walkways, a 61-edge / 25-block route on <code>?w=40.7128,-74.0060;40.7180,-74.0020</code>, four presses each): click → block heat <strong>53–105ms → 2.3–6.2ms</strong>; click → first WebGL draw <strong>68–118ms → 25.9–39.2ms</strong>; main thread blocked in the 6s after the press <strong>1110–1126ms → 51–59ms</strong>; corridor recompute <strong>1205ms (9/9 types) → 65–73ms (1/9)</strong>.",
    "The heat now moves <strong>before the POST is even sent</strong> — the timeline reads <code>block-votes changed=24</code> at t=2.7ms, <code>POST /vote sent</code> at t=4.0ms, <code>--sync end--</code> at t=4.3ms. It is in the press's own tick, not merely earlier.",
    "<strong>The prediction is exact.</strong> When the server's delta lands ~100ms later it logs <code>heat apply (diff): 0 writes</code> — the authoritative SET found nothing to change on any of the 25 blocks. That is the invariant that keeps the vote from visibly correcting itself, and it held on every press of every run.",
    "Same shape on a different map and a different selection kind: a POINT cast on <code>nyc-proposals</code> moves its 1 block at t=5.1ms and its confirming delta is likewise a <code>0 writes</code> no-op.",
    "<strong>The corridor cache is correct, not just fast:</strong> <code>perf/vcast-verify.mjs</code> reports <code>incremental: 20 corridors in 68.2ms (1/9 types clustered)</code> versus <code>full: 20 corridors in 1188.1ms (9/9 types clustered)</code> — and <code>lists match: true</code> on the ids and scores.",
    "The cast-state highlight was checked rather than assumed: it flips at 22–28ms both before and after, already driven by the synchronous store write. No change was needed and none was made.",
    "<strong>645 tests pass</strong> (37 files, 1 skipped), including 24 new ones in <code>optimisticVote.test.ts</code>; <code>npx tsc -b</code> is clean; <code>eslint</code> reports the same 4 pre-existing errors as <code>HEAD~1</code> and no new ones.",
    "Not deployed. Local only, per the brief.",
]

CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-walkways</code>, draw a route, and press <code>+</code> — the blocks under the route should change colour in the same instant the button turns, with no second nudge when the server answers.",
    "Press <code>+</code> again to unvote: the heat should come back DOWN by the same amount it went up. If a second press ever brightened it further, the dedupe prediction would be wrong.",
    "Press <code>−</code> on a route you have already upvoted — the heat should swing across (one vote moving arms), not add a downvote on top of the upvote.",
    "Watch the debug console (<code>?tab=vote</code>) while pressing: expect <code>[blocks] heat apply (diff): N writes</code> in the click's tick and then <code>0 writes</code> when the delta arrives. A nonzero second number means the prediction missed and the map visibly corrected.",
    "Kill Flask (or go offline in DevTools) and press <code>+</code>: the vote should appear, then undo itself, and the bottom toast should read &quot;Couldn't save your vote — please try again.&quot; Bring Flask back before the next check.",
    "With Flask still down, press <code>+</code> then <code>−</code> quickly: the control should settle on <code>−</code>'s rollback state, not flip back to what the first press saw.",
    "<code>cd client-react &amp;&amp; npx vitest run src/utils/optimisticVote.test.ts</code> — 24 tests, including the two background-refresh races.",
    "Re-derive the numbers yourself: <code>cd perf &amp;&amp; node vcast.mjs --map nyc-walkways --label \"Widen sidewalk\"</code> and <code>node vcast-verify.mjs</code>.",
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
    "client-react/src/utils/castVote.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · vote data path", "The ONE cast path: every +/− in the app (top bar, route card, proposal pin) goes through castVotes — plan, optimistic apply, POST, reconcile, roll back"),
        "file": ("castVote.ts", "~470 LOC — the block-scoped press rule, the optimistic event, the request, and every undo path"),
        "outline": [
            ("imports", "+ blockSelection (TouchedBlock), + pendingVotes; − blockCoverage/myVotesInBlocks (folded into one pass)", True),
            ("pinPendingSticker", "unchanged — binds a scanned QR code to the proposal a cast landed on", False),
            ("OptimisticVoteDetail", "reshaped — ONE event per press carrying every transition + the block prediction", True),
            ("CastResult / TransitionGroup", "unchanged", False),
            ("BlockVotePlan", "+ blockDeltas — the predicted deduped block moves", True),
            ("presenceIn / blockVoteDeltas", "new — the server's dedupe rule, predicted", True),
            ("planBlockVote", "rewritten — one pass gives coverage, my votes AND the block prediction", True),
            ("voteButtonState", "unchanged — the red/blue highlight was already optimistic", False),
            ("dispatchOptimistic / invertGroups / revertGroups / announceRollback", "new helpers — the rollback that recomputes and speaks", True),
            ("groupByPrevDir", "unchanged — partitions edges by the direction they held", False),
            ("castVotes: plan + optimistic apply", "one event, then the store write, then the pending registration", True),
            ("castVotes: the POST", "+ `announced` so a rollback message never doubles a server reason", True),
            ("castVotes: capped / evicted", "routed through revertGroups; declined edges stop shielding", True),
            ("castVotes: success", "+ confirmPendingCast — no longer rollback-able, still shielding", True),
            ("castVotes: catch", "+ superseded check, + block revert, + the toast", True),
        ],
        "blocks": [
            "blockVoteDeltas is a difference of two BOOLEANS per arm, not a sum of edges — that is the whole dedupe rule",
            "before() reads the store, after() reads the change map first: so the SAME function serves the forward plan and the rollback, just with a different map",
            "planBlockVote's single pass replaces blockCoverage + myVotesInBlocks + a getVote per selection edge",
            "active = blocks.length > 0 && atDirection === blocks.length — preserves blockCoverage's 'all' semantics exactly, empty set included",
            "The optimistic event fires BEFORE setVotes, so a listener sees the pre-write store; the block prediction is already computed by then",
            "registerPendingCast happens before fetch(), not after — a refresh can land during the request itself",
            "revertGroups recomputes its block deltas rather than negating the forward ones: a rollback lands later, against a store that may have moved",
            "isLatestPendingCast is what stops a rollback racing the next click on the same control",
            "A capped edge is deleted from the shield: the server never took it, so a snapshot omitting it is truth, not lag",
        ],
    },
    "client-react/src/utils/pendingVotes.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · vote data path", "The ledger that makes an optimistic write distinguishable from a confirmed one for the window between dispatch and echo"),
        "file": ("pendingVotes.ts", "new, ~166 LOC — register / confirm / settle, plus the two queries the refresh paths ask"),
        "outline": [
            ("module header", "new — names both refresh paths and why neither could tell the two apart", True),
            ("PENDING_CAST_TTL_MS", "new — 20s backstop for a socket that dropped the delta", True),
            ("PendingTransition / PendingBlockDelta / PendingVoteCast", "new — the shapes the store and the graph re-apply read", True),
            ("registerPendingCast / confirmPendingCast / resolvePendingCast", "new — the lifecycle", True),
            ("isLatestPendingCast", "new — has a later press on this control superseded me?", True),
            ("pendingCastsFor / pendingDirection / hasPendingCasts", "new — what the store and fetchVotes ask", True),
            ("settlePendingCastsForDelta", "new — the echo, matched on mode + type + a shared edge", True),
            ("_resetPendingCasts", "new — test-only", True),
        ],
        "blocks": [
            "Two phases, not one: `confirmed` (the POST returned) still shields but can no longer roll back",
            "The control key is (mode, vote type) — the grain at which one press supersedes another",
            "pendingDirection lets a LATER cast win when two touch the same edge",
            "settle matches on an edge INTERSECTION because the server publishes exactly the edges it changed",
            "The TTL is a backstop, not the mechanism: a live socket settles in ~50ms",
        ],
    },
    "client-react/src/utils/voteStore.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · vote data path", "The single source of truth for 'how this browser has voted' — the packed per-(mode, edge, type) direction map behind every +/− button's cast state"),
        "file": ("voteStore.ts", "~430 LOC — persistence, label→id resolution, the change signal, coverage, and server reconciliation"),
        "outline": [
            ("imports", "+ pendingVotes (type-only cycle-free)", True),
            ("persistence / label→id migration", "unchanged", False),
            ("notify / subscribeVotes / getVotesVersion", "unchanged — the one signal every vote view re-renders on", False),
            ("getVote / writeVote / setVote / setVotes", "unchanged", False),
            ("coverage / blockCoverage / myVotesInBlocks", "unchanged — still the button-state readers", False),
            ("reconcileEdge", "+ skips any (edge, label) an in-flight cast holds", True),
            ("resetMapVotes", "+ replays in-flight casts after applying server truth, and says so in the log", True),
            ("_resetVoteStore", "unchanged", False),
        ],
        "blocks": [
            "Server truth is still applied FIRST and still deletes — the stale-localStorage failure it exists for is unchanged",
            "The in-flight replay runs LAST, so it wins over the snapshot rather than fighting it",
            "reconcileEdge skips per (edge, label), not per response: an unrelated edge in the same body still applies",
            "The log line now reports 'restoring N in-flight' — the brief's `dropped 4046 / applying 2133` line is exactly where this bug was visible",
        ],
    },
    "client-react/src/utils/blockSelection.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · block-scoped selection", "Materializes a selection's touched blocks and sums their deduped counts — the aggregation grain everything about voting is expressed in"),
        "file": ("blockSelection.ts", "~230 LOC — the route-votes cache key, block materialization, and the modal's count/coverage rows"),
        "outline": [
            ("ROUTE_VOTES_CACHE_MAX / routeVotesKey", "unchanged", False),
            ("TouchedBlock", "new — a block's edges AND the key that names it", True),
            ("materializeTouchedBlocks", "new — the keyed form; the old function is now a projection of it", True),
            ("materializeBlocks", "unchanged behaviour — kept for the coverage readers that never name a block", True),
            ("singletonBlocks", "new — the no-block-layer fallback, keyed the way touchedBlockKeys names one", True),
            ("selectionVoteRows / selectionCoverage", "unchanged", False),
        ],
        "blocks": [
            "The prediction needs the block ID, not just its edges — that is what names the aggregate the server will move",
            "One object rather than two parallel arrays, so keys cannot drift out of step with edges",
            "singletonBlocks uses touchedBlockKeys' own negative encoding (−e−2), so nothing downstream can mistake a stand-in for a real block",
            "Subarray views are preserved — the mobile typed-array memory rule still holds",
        ],
    },
    "client-react/src/components/GraphLayer/voteApply.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · in-memory vote arrays", "The pure mutators behind the heatmap: edge/node/block counts, and topProposalDiffs, the signed number behind every block's colour"),
        "file": ("voteApply.ts", "~340 LOC — four apply functions and the heat derivation"),
        "outline": [
            ("legendIndex / rederiveNodes", "unchanged", False),
            ("applyMyVoteChange", "unchanged — the optimistic EDGE path", False),
            ("applyEdgeVoteChange", "unchanged — the legacy increment fallback", False),
            ("applyMyBlockVoteChange", "new — the optimistic BLOCK path, the increment twin of the SET", True),
            ("applyBlockCounts", "unchanged — the authoritative block SET", False),
            ("topProposalDiffs", "unchanged — turns block counts into signed heat", False),
            ("applyAuthoritativeCounts", "unchanged — the authoritative edge SET", False),
        ],
        "blocks": [
            "Same bookkeeping as applyBlockCounts on purpose: block_votes stays total deduped activity, the breakdown stays sorted by total desc",
            "Matching that bookkeeping is what makes the confirming SET a no-op instead of a re-sort the eye can catch",
            "Clamped at zero, and compared AFTER clamping — a move that clamps away must not report a change and repaint nothing",
            "Returns whether the block layer moved, so the caller knows whether to re-broadcast the heat",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client · graph + heat layer", "Owns the topology, the vote arrays, the WebSocket delta stream, the block-heat broadcast, and both proposal families"),
        "file": ("GraphLayer.tsx", "~5,400 LOC — the map's whole data and interaction surface"),
        "outline": [
            ("imports", "+ materializeTouchedBlocks / singletonBlocks / applyMyBlockVoteChange / pendingVotes", True),
            ("voteEpochRef / bumpVoteEpoch", "+ dirtyVoteLabelsRef — the epoch now carries WHICH types moved", True),
            ("setBlockMaterializer", "hands RouteContext keyed blocks", True),
            ("broadcastBlockVotes", "unchanged — topProposalDiffs → the block-votes event", False),
            ("routeCacheRef / recomputeRouteProposals", "per-type invalidation instead of a full clear", True),
            ("recomputeTopProposals (PBTP)", "unchanged", False),
            ("scheduleOwnCastRecompute", "unchanged — still the 400ms own-cast debounce", False),
            ("fetchVotes", "+ re-applies in-flight casts, and broadcasts heat AFTER the replay", True),
            ("applyDeltaToGraphData", "+ bumps with the delta's label, + settles the pending cast it echoes", True),
            ("applyCastToGraphData", "new — the one apply the press and the re-apply share", True),
            ("optimistic-vote listener", "rewritten — applies the block prediction and repaints the heat", True),
            ("castProposalVote / castRouteVote", "pass keyed blocks", True),
            ("my-votes effects", "unchanged — the store is now pending-aware on their behalf", False),
            ("hover / selection / markers / RBTP rendering", "unchanged", False),
        ],
        "blocks": [
            "applyCastToGraphData is shared deliberately: the press and the post-refresh re-apply MUST write the same thing or they would disagree about what is on screen",
            "broadcastBlockVotes(data, 'delta') — 'delta' is the source that animates arrival, which is what an own cast should do",
            "The confirming delta then finds nothing to change, so it does not re-broadcast and the animation does not run twice",
            "bumpVoteEpoch(labels?) : no argument still means 'everything dirty', which is what a whole-snapshot install wants",
            "eligibleLegendLen distinguishes 'a type the job ruled out' (delete its clusters, a no-op) from 'a type the job never saw' (rebuild the list)",
            "fetchVotes' broadcast moved AFTER the delta replay + pending re-apply, so the heat painted is the one that includes them",
        ],
    },
    "client-react/src/context/RouteContext.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client · selection + cast context", "Owns the canonical Selection and the top bar's cast: currentEdgeIds, the effective vote type, castVote and isDirectionCast"),
        "file": ("RouteContext.tsx", "~1,800 LOC — selection state, routing, URL sync, and the cast controls"),
        "outline": [
            ("imports / BlockMaterializer", "now returns TouchedBlock[]", True),
            ("selection reducer / URL sync / routing", "unchanged", False),
            ("currentEdgeIds / effectiveVoteType", "unchanged", False),
            ("castVote", "unchanged — already fire-and-forget through castVotes", False),
            ("isDirectionCast", "reads .edges off the keyed blocks", True),
            ("provider value", "unchanged", False),
        ],
        "blocks": [
            "The top bar's cast is the control the brief is about, and it needed no change here: it was already optimistic and already fire-and-forget",
            "Only the block SHAPE changed, so the same materializer serves both the cast and the button's pressed state",
        ],
    },
    "client-react/src/utils/optimisticVote.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · tests", "The cross-module story castVote.test.ts does not reach: prediction vs the server's rule, reconciliation, rollback"),
        "file": ("optimisticVote.test.ts", "new, ~375 LOC — 24 tests over castVote + pendingVotes + voteStore + voteApply"),
        "outline": [
            ("blockVoteDeltas — the dedupe rule, predicted", "7 tests — once per block, second press, flip, extension, cancel, multi-block, singleton", True),
            ("optimistic apply vs the authoritative SET", "5 tests — the no-op invariant, a wrong prediction, the clamp", True),
            ("surviving a background refresh", "5 tests — both refresh paths, mid-flight and post-POST, and the settle", True),
            ("rollback", "7 tests — store + blocks, the toast, the passcode gate, the superseded press, the cap", True),
        ],
        "blocks": [
            "Node's own EventTarget/CustomEvent stand in for window, so listener semantics are real rather than mocked",
            "The mid-flight test gates fetch on a promise, so the refresh lands strictly between the optimistic write and the response",
            "A second test covers the harder race: the refresh lands AFTER the POST returned but before the echo — versionAtFetch cannot see that one",
            "The rollback test applies forward then backward deltas to a graph and asserts it is byte-identical to where it started",
        ],
    },
    "client-react/src/utils/castVote.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · tests", "The docs §4.2 press matrix and the wire contract"),
        "file": ("castVote.test.ts", "~240 LOC — 19 tests, unchanged in intent"),
        "outline": [
            ("fixtures", "singles() now returns keyed singletons; blk() names a real block", True),
            ("planBlockVote — singleton blocks", "expectations gain blockDeltas: []", True),
            ("planBlockVote — real blocks", "fixtures carry block ids; one expectation gains its delta", True),
            ("voteButtonState / castVotes", "unchanged in intent", True),
        ],
        "blocks": [
            "Every pre-existing assertion about the press matrix still holds verbatim — the rule did not change, only what the plan additionally reports",
        ],
    },
    "perf/vcast.mjs": {
        "on": ["React / Leaflet client"],
        "module": ("Perf · harnesses", "Playwright harnesses that measure real user-visible latency in the running app — vtoggle.mjs for the legend, vcast.mjs for a press"),
        "file": ("vcast.mjs", "new, ~233 LOC — instrument, press, summarize"),
        "outline": [
            ("INIT: frame timeline / longtask observer", "new", True),
            ("INIT: WebGL clear hook", "new — MapLibre draws only when dirty, so a draw IS the repaint", True),
            ("INIT: block-votes listener", "new — counts how many blocks each broadcast actually moves", True),
            ("INIT: fetch + WebSocket hooks", "new — the POST round trip and the echo", True),
            ("INIT: cast-class MutationObserver", "new — when the red/blue highlight flips", True),
            ("summarize / CDP latency emulation", "new — the per-press table", True),
        ],
        "blocks": [
            "Modelled on vtoggle.mjs so the two are read the same way",
            "heatDeltaMs counts only a broadcast that MOVED a block — a broadcast that changes nothing is not a visible change",
            "--latency emulates a slow network via CDP; note the already-open WebSocket is not throttled by it, so the echo stays fast in that mode",
        ],
    },
    "perf/vcast-verify.mjs": {
        "on": ["React / Leaflet client"],
        "module": ("Perf · harnesses", "The correctness twin of a perf change — the pattern vtoggle-verify.mjs established for e3086cc"),
        "file": ("vcast-verify.mjs", "new, ~66 LOC — two sessions, one comparison"),
        "outline": [
            ("session A", "new — cold load, press, capture the INCREMENTAL corridor list", True),
            ("session B", "new — fresh load in that same vote state, capture the FULL list", True),
            ("compare + restore", "new — ids and scores must match; the press is undone so the run repeats", True),
        ],
        "blocks": [
            "A cache that is fast and wrong is worse than no cache, and only a cold load re-clusters every type from scratch",
            "The lists are compared on ids AND scores, not just length",
            "It puts the map back where it found it, so the check is repeatable",
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

  <p class="lede">Pressing <code>−</code> or <code>+</code> took about a second to show anything, and it was never the request — the POST answered in ~20ms. It was two other things. The map waited for the WebSocket echo of its own vote, because the optimistic apply moved the EDGE counts while a block-layer map paints the BLOCK ones; and every press then forced a full re-cluster of all twelve vote types, ~1.1s of long tasks landing right on top of it. So the press now predicts the deduped block move exactly — exactly enough that the server’s confirmation lands as a literal no-op rather than a visible correction — an explicit in-flight ledger keeps a background refresh from walking over it, a failure undoes itself and says so without being able to race the next click, and a cast invalidates one vote type’s corridors instead of all of them. Click → heat: <strong>53–105ms → 2.3–6.2ms</strong>; main thread blocked after a press: <strong>~1,120ms → ~55ms</strong>.</p>

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
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_2026-08-18-optimistic-vote.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes-optimistic-vote.diff &amp;&amp; python changelog/build_2026-08-18-optimistic-vote.py</code>.
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
