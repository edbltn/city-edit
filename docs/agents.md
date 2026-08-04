# Agents

Every way this project has used agents — the simulated-user fleets that load-tested the app to its
breaking point, and the Claude agents that built, reviewed, and verified the code.

*Compiled 2026-08-03 from git history (334 commits), `changelog/`, `docs/`, and `.claude/`.*

There are two distinct "agent" programs in this repo's history, and they're worth separating:

- **A. Simulated-user agents** — load/stress/correctness harnesses that pushed the running app
  until it broke, then proved the fixes.
- **B. LLM (Claude) agents** — workers that built, reviewed, and verified the code itself.

---

# A. Simulated-user agents — pushing the app to its limits

## A1. Locust commuter agents — `loadtest/locustfile.py`
*2026-06-01 · commit `4910b45` "feat(loadtest): locust load test for the voting websocket"*

The first fleet. Each `DesirePathUser` behaves like one realistic commuter:

1. Holds an open `/ws?map=<slug>` connection — exercising the WebSocket fan-out path, explicitly
   modeled on the One Million Checkboxes write-up's finding that broadcast is the real scaling
   bottleneck for this class of app (one vote → N deltas → N clients).
2. Picks two nearby points clustered on real neighborhoods, routes between them via `/api/routes`
   (OSRM), then votes on the returned edge ids — so paths light up like genuine commutes.

Vote behavior is randomized across three tasks: `route_cast` (bulk "I travel here" upvote),
`upvote_proposal` (directional +1), and `downvote_proposal` (directional −1, *usually reversing one
of this user's own earlier casts* — which is what exercises `apply_directional` reversal). Each
agent carries a unique `voter_id` so reversal behaves like distinct people rather than one shared
voter.

**Custom WS metrics were the point:**

- `WS / connect` — handshake time
- `WS / delta recv` — every delta the fleet received (raw fan-out volume)
- `WS / self-delta` — round trip from a user's own vote POST to seeing that vote echoed back over
  its own socket, i.e. **real end-to-end broadcast latency under load**

Map-driven: at startup it fetches `/api/maps/<MAP>` to learn the map's vote mode, its route
vote-type labels, and the city geography, so the same file works against any map.

Separation of concerns, per the README: *behavior* lives in `DesirePathUser`; *load* is just how many
you spawn. The same file scales from 1 user (eyeball the map blooming) to hundreds.

```bash
make loadtest-local                              # web UI, charts at :8089
make loadtest-local USERS=10 RATE=2 TIME=5m      # headless
make loadtest-prod  USERS=25 RATE=5 TIME=3m      # wss:// derived from the https host
```

A June-1 SF run is still archived: `loadtest/sf_run.html`, `sf_run_stats.csv`,
`sf_run_stats_history.csv`, `sf_run_failures.csv`, `sf_run.log`.

## A2. Stateful correctness under concurrency — `loadtest/verify_loadtest.py`
*`make loadtest-verify` · documented in `docs/testing.md`*

Not throughput — **correctness**. Unlike the Locust run, this asserts that concurrent voting
converges to a known state.

Each of N agents is assigned a deterministic expected final direction for every edge of a shared
route (`FINALS = [1, -1, 0]` cycled over `(agent_i + edge_j)`), plus a deliberately **convoluted
path to get there**: cast for → flip against → flip back → then settle, *including removals*. All
agents march concurrently through the real `/api/vote` endpoint. Then two independent verifications:

1. **Per-agent** — `GET /api/my-votes` returns exactly each agent's final direction.
2. **Aggregate** — the `/api/graph-votes` net per edge moved by exactly the sum of agents' final
   directions, measured as an **after − before delta** so pre-existing votes on those edges don't
   matter.

Exit code is non-zero if either check fails. Seeded with real city coordinates
(`CITY_SEEDS` for sf / nyc / chicago) and map-driven vote-type discovery.

```bash
make loadtest-verify                          # 10 agents vs localhost:8080
make loadtest-verify USERS=25 HOST=https://…
```

## A3. In-process twin — `server/tests/integration/test_multi_agent_convergence.py`

The same convoluted routine at unit-test speed: 10 agents against fakeredis + a dict "DB",
reproducing the server's `/api/vote` inner loop (read prior per-voter direction → `apply_directional`
→ persist), with **steps interleaved round-robin** to mimic concurrency (step 0 of every agent, then
step 1 of every agent…). Asserts the Redis aggregate converges to the sum of each agent's final
per-edge direction — the same property the live load test checks end to end, but runnable in CI
without services.

## A4. Tiered saturation test — `server/tests/loadtest_votes.py`
*Report: `changelog/2026-07-08-agent-load-test.html` · commit `06f5583`*

Tiers of **5 → 10 → 20 → 40 → 80 → 160** agents against a throwaway map (`agent-test-1`, created via
`POST /api/maps`). Each agent joins like a real client: opens `/ws?map=…` and counts pushed deltas,
fetches `/api/graph-votes` once, then runs six cycles of `POST /api/routes` (random Manhattan-core
origin/destination) → `POST /api/vote` on the returned edge set (~100–200 edges, i.e. a real
multi-block route vote with clear-then-cast semantics).

Crucially it runs a **sidecar probe** hitting `/health` every second and sampling Flask CPU/RSS, so
lockup is detectable independently of agent traffic.

### Results

| Tier | Agents | Wall | Vote p50 | Vote p95 | Vote fails | Votes/s | graph-votes p50 | WS msgs | RSS peak | CPU peak |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 5   | 6.5 s   | 0.29 s  | 0.93 s      | 0/30    | 4.6 | 1.31 s | 150     | 3,537 MB | 99 % |
| 2 | 10  | 8.9 s   | 0.72 s  | 1.03 s      | 0/60    | 6.7 | 1.31 s | 582     | 3,656 MB | 101 % |
| 3 | 20  | 15.2 s  | 1.76 s  | 2.10 s      | 0/120   | 7.9 | 1.25 s | 2,263   | 3,698 MB | 103 % |
| 4 | 40  | 27.8 s  | 3.79 s  | 4.26 s      | 0/240   | 8.6 | 1.51 s | 8,942   | 3,744 MB | 101 % |
| 5 | 80  | 58.4 s  | 8.37 s  | 9.54 s      | 0/480   | 8.2 | 3.08 s | 35,305  | 5,167 MB | 122 % |
| 6 | 160 | 146.9 s | 16.57 s | **45.63 s** | 129/960 | 5.7 | 3.97 s | 132,808 | **7,066 MB** | 165 % |

The app never hard-crashed, but the vote path **saturated at ~8–9 votes/s from Tier 3 onward** —
every additional agent just deepened the queue. At Tier 6 it functionally locked up: 13 % of votes
blew the 45 s client timeout, 11 of 160 `graph-votes` responses were truncated mid-body
(`ClientPayloadError`), RSS doubled to 7.1 GB.

### What breaks, in order

1. **One process-wide lock serializes every vote** *(critical — the lockup)*. `cast_vote` took a
   single `threading.Lock` (`_proposal_vote_lock`) around the whole clear-then-cast transaction,
   serializing all voters on *all maps* in the instance. With ~90–100 ms of work inside, the hard
   ceiling is ~10 votes/s and queue latency is `depth × 100 ms` — exactly the measured linear p50
   series (0.29 → 0.72 → 1.76 → 3.79 → 8.37 → 16.6 s).
2. **~300–550 sequential Redis round trips per vote, inside that lock** *(why the lock is slow)*.
   `apply_directional` ran a 2-command pipeline **once per edge** (~145 RTTs for a typical route),
   and `apply_block_delta` issued 1–3 unpipelined `HINCRBY`/`HDEL` per edge (~150–400 more), plus a
   synchronous Postgres read + delete + 145-row insert and a post-write count read-back.
3. **`graph-votes`: 33.7 MB per join, cache defeated by voting** *(the join storm)*. The LRU vote
   cache is keyed by revision — and every vote bumps the revision — so under sustained voting every
   fetch is a full rebuild, each buffering the full body per request.
4. **WS fan-out is O(votes × clients)**, one thread + one Redis pubsub connection per client polling
   at 10 Hz. 132,808 messages for 831 votes at Tier 6; 160 polling threads fighting the GIL is why
   routes p95 rose from 30 ms to 2.1 s (OSRM itself was never the bottleneck).

**The blind spot it exposed:** `/health` stayed at p50 1–2 ms in *every* tier (worst single probe
1.8 s), and all 160 WebSockets stayed connected throughout. Monitoring would not have noticed the
outage.

**The fix it drove** (`changelog/2026-07-09-vote-path-mitigations.html`, prod rev 00088): batched
Redis writes (≤3 pipelines per vote), 256 striped voter locks, merged `delta_hub` WS fan-out,
debounced gzip snapshots with the etag equal to the served body, and a shed valve + admin saturation
metrics. Re-run at 160 agents: **p50 16.57 → 1.25 s, 5.7 → 45.7 votes/s, RSS peak 7.1 → 4.0 GB.**

Regression command:
`server/tests/loadtest_votes.py --agents 160 --flask-pid <pid>`.

## A5. Multi-tenant go/no-go swarm — `server/tests/swarm_interactions.py`
*commits `2faac4a`, `2a6696f`, `edecbf9` · report `changelog/2026-07-13-map-load-oom-and-scaling.html`*

The most deliberate harness. **30–40 agents spread across every live public map** (fetched from
`/api/maps`, weighted toward `--primary`, default `nyc-walkways` — 30 agents over ~16 maps ≈ the
30-tenant target). Each agent replays the full client sequence:

```
GET /api/maps/<slug>                  (map meta)
GET /api/graph-version                (cache key)
GET /api/graph-topology?format=bin    (the big one — GTB2 blob)
GET /api/graph-votes                  (vote arrays + blocks)
WS  /ws?map=<slug>                    (held open for the session)
loop: POST /api/routes → POST /api/vote → GET /api/reverse-geocode
```

Every interaction is timed individually against a `(hard timeout, p95 budget, max error rate)`
triple, and **the run exits 1 if any interaction class blows its budget or errors** — from the
module docstring: *"the point is a deployable go/no-go signal, not vibes."*

```python
BUDGETS = {
    "map_meta":        (10,  1.0, 0.02),
    "graph_version":   (10,  1.5, 0.02),
    "topology_bin":    (120, 90.0, 0.02),   # bandwidth-aware, see below
    "graph_votes":     (30,  5.0, 0.02),
    "ws_connect":      (15,  5.0, 0.05),
    "route":           (20,  3.0, 0.05),
    "vote":            (20,  3.0, 0.02),
    "reverse_geocode": (15,  3.0, 0.05),
}
```

Two design details worth calling out:

- **Tenancy safety.** `may_vote(slug)` restricts synthetic casts to the primary map and `test-*`
  maps; every other real tenant map gets the full *read* path (load, topology, votes, WS, routing)
  without polluting production vote data.
- **Bandwidth honesty** (`edecbf9` "test(swarm): bandwidth-aware topology budget"). The
  `topology_bin` budget was widened to 90 s with a comment explaining that N agents × ~18 MB from a
  single test host share that host's downlink and finish together, so client p95 ≈ N × size /
  bandwidth *regardless of server health*. Judge that row by errors + Cloud Run `httpRequest.latency`
  instead. The harness documents where it stops measuring the server.

### What it caught (2026-07-13, the 30-tenant scaling push)

| | before | after |
|---|---|---|
| `graph_votes` p95 (40-agent swarm, local) | 13.3 s | **0.64 s** |
| `routes` p95 (40-agent swarm, local) | 9.2 s | **0.33 s** (worst p95 of any interaction) |
| `/api/maps/<slug>` under a 40-agent join wave (prod) | **60 % client timeouts** | p95 0.85 s |
| NYC topology gzip | ~0.4 core-s **per request** (nginx) | once at prewarm, 18.8 MB served verbatim |
| cheap endpoints during a prod join wave | 1.4–2.3 s p95 (CPU-starved by gzip) | graph_version 0.55 s · map_meta 0.85 s |

Concretely: `vote_store.build_arrays` was pure Python over all edges + nodes on every snapshot
rebuild → sparse/vectorized (signature now takes `edge_ends` `[e,2] int32`, not `node_adj`); the
`/api/maps/<slug>` stampede was `fetch_voted_vote_type_labels` doing a `GROUP BY` over 674 K
`edge_votes` rows per request → composite index `idx_edge_votes_map_vt_created` (applied to prod with
`CREATE INDEX CONCURRENTLY`) + a 30 s TTL cache with a per-slug single-flight lock. It also surfaced
that prod had drifted to `maxScale=1` from a stale resnap pin.

**It ran against prod across three deploy iterations** — 00094 (60 % map-config timeouts) → 00096
(gzip CPU tail) → **00097: 40 agents / 16 maps / 10 cycles, ALL BUDGETS MET, zero errors across
~1,150 timed interactions.**

```bash
python swarm_interactions.py --base https://cityedit.org --agents 30 --cycles 5
python swarm_interactions.py --base http://localhost:5001 --agents 40
```

## A6. `scripts/test-cloud.sh`
A lightweight smoke agent for the deployed instance — health check, WebSocket upgrade shape (400/426
without headers is a pass), basic endpoint availability. `make test-cloud`.

---

# B. LLM agents — building, reviewing, verifying

## B1. A standing subagent roster — `.claude/agents/*.md`
*commit `b000e45` "docs: deploy/architecture notes, agent config, and worktree scripts"*

Five project-specific agents, each with a model tier and a `PROACTIVELY use` trigger in its
description:

| Agent | Model | Role |
|---|---|---|
| `api-tester` | haiku | curl the geocode / reverse-geocode endpoints, assert status codes + JSON schema, PASS/FAIL per endpoint |
| `verify-app` | haiku | redis ping → Flask imports → React builds → `.env` present → HEALTHY / UNHEALTHY with fix recommendations |
| `code-reviewer` | inherit | reads `git diff` against the CLAUDE.md Python/JS/CSS checklists + a security pass; reports Critical / Warning / Suggestion |
| `debugger` | sonnet | this project's actual failure catalog is baked in: Redis connection, OSRM-down-falls-back-to-python_router, WS disconnects behind nginx, CORS. Required output: root cause / evidence / fix / verification |
| `docker-helper` | sonnet | compose builds, service health, inter-service networking |

## B2. Worktree infrastructure for parallel agents
*`scripts/new-worktree.sh`, `scripts/land-worktree.sh`, `.claude/skills/worktree/SKILL.md`,
commit `f5dbc81` "chore(dev): allow worktree stacks on alt ports"*

Git worktrees (no Docker) so multiple Claude agents work on different PRDs at once. `new-worktree.sh
<slug>` creates a sibling `<repo>-<slug>-<hash>` on its own branch sharing the main checkout's
`.git`, then:

- copies the gitignored `server/.env`,
- runs `npm install`, builds the Python venv with `uv pip`,
- and **starts the client dev server on the next free incremented port** (3001, 3002, …) in the
  background, logging to `<dir>/dev-server.log` — so several agents can preview the frontend in
  parallel. Dev-mode detection keys off Vite's `DEV` flag, not the port, so any port still talks to
  Flask on `:5001`.

The skill documents the honest limitation: **the backend is single-instance.** Only one
Flask/Redis/Postgres (5001/6379/5432) and one Docker stack (8080) can bind at a time, so all worktree
dev servers share whatever backend is running — fine for frontend/UI work, must be flagged when a
change needs an isolated backend. `server/osm_data/` (~1 GB) is git-tracked so it appears in each
worktree for free.

`land-worktree.sh <dir>` rebases the branch onto `main` (linear history, no merge commit),
fast-forwards `main` in the main checkout, removes the worktree, deletes the branch. It refuses to
run on a dirty tree, and conflicts are surfaced to the user rather than force-resolved.

## B3. Delegation-wave planning — `docs/archive/unify-voting-waves.md`

Copy-pasteable agent prompts arranged as an explicit **dependency graph**:

```
Wave 1 (parallel):  W1-A edge_block_id     W1-B FE Cluster types    W1-C BE Cluster types
Wave 2 (parallel):  W2-A block_votes+propagate(←A,C)  W2-B backfill(←A)  W2-C FE block-select(←A,B)
Wave 3 (parallel):  W3-A route UX parity(←2C)  W3-B modal summary(←2A,2C)  W3-C issue-4(←1B,1C)  W3-D verify(←all)
```

With **file ownership assigned per agent to prevent merge conflicts** — "W1-A owns graph build/load +
new `server/blocks.py`, does NOT touch `route_proposals.py`; W1-C owns `route_proposals.py`; W2-A owns
`block_votes.py` + the `/api/vote` write loop; W2-B owns a new migrate script. Run each in its own
worktree." Every prompt repeats a short SHARED CONTEXT block (repo, branch, what to read first,
uv-only Python, don't deploy) and must end with a 5-line summary of what changed + how to verify.

**Now archived** — the central design decision it encodes (fan a vote across every edge of its block
at write time) was later reversed, and `docs/archive/README.md` explicitly warns the prompts must not
be reused. Superseded by `docs/three-layer-model.md`.

## B4. The overnight Haiku swarm — 2026-07-23
*commit `562162b` · report `changelog/2026-07-23-nightly-swarm.html` · branch `nightly/2026-07-23`*

The most ambitious multi-agent run: **13 Haiku workers → 11 Sonnet reviewers → 1 integrator**, a
nightly-maintenance backlog executed unattended.

Structure:

1. Each task went to a **Haiku worker in an isolated git worktree**, committing to its own
   `nightly/0723-Txx` branch. (Those 13 branches, plus 13 `worktree-wf_265f398b-7dc-*` workflow
   branches, are still in the repo.)
2. Each branch was read **read-only** by a **Sonnet reviewer** in the main checkout, which approved
   or rejected.
3. Only approved branches were cherry-picked onto `nightly/2026-07-23`, **each gated by a test
   baseline recorded before dispatch** (68 passing + 7 known-failing pytest, 258 vitest, clean tsc).
   A branch is kept only if it adds no new failures versus that list.

### 5 landed

- **T01** — the backend suite went fully green for the first time on the branch (75/75, was 68/7).
  The 7 failures were *tests drifted from the code*: `build_arrays` and `encode_topology_bin` had been
  refactored to take `edge_ends` (`np.ndarray [e,2] int32`) instead of per-node `node_adj` lists
  during the swarm-era vectorization (§A5), while the fixtures still passed lists and compared numpy
  arrays with `==`.
- **T03** — client suite 258 → 297 tests (mapStyles / themes).
- **T13** — 615 lines of pure helpers extracted out of `GraphLayer.tsx` into four focused modules
  (spatialLookup / geometryHelpers / topologyHelpers / geocodingHelpers).
- **T08** — `three-layer-model.md` caught up with differential block heat.
- **T04 (salvage)** — rejected in review, but its verified-safe parts (~600 KB of stray root
  screenshots) were re-done by hand at integration.

### 8 rejected — the valuable part

**9 of 13 worktrees were silently snapshotted from a seven-week-stale commit** (`ca9ed56`,
2026-06-02, 168 commits behind dispatch HEAD). The workers then "verified" against the wrong tree:

- **T02**'s "42 passed" was only reproducible because the worker had **manually copied an
  uncommitted `graph_arrays.py` into its worktree** — unreproducible from the branch alone.
- **T06**'s sweep "scanned all 13 server files" of a tree that has 25+, and its one edit
  **resurrected the deleted `import_citibike.py`**.
- **T04** grepped a tree where `ebikes.png`'s referencing file didn't exist yet and declared it
  unreferenced.
- **T09** groomed a seven-week-old `TODO.md`.
- **T05 and T11** finished without reporting at all; their orphan commits were inspected by hand —
  both stale-based (T05 deleted files that no longer exist; T11 rewired WebSocket/drag hooks against
  old code) and dropped.

**The reviewers caught every one.**

### Rules encoded for next time

- Any agent working in a spawned worktree must **first assert
  `git merge-base <dispatch-sha> HEAD == <dispatch-sha>`** and abort if not. Reviewers check
  merge-base **before reading a single diff line** — it instantly explained every bogus claim.
- **Never trust worker test evidence unless it reproduces from the committed branch alone.** A clean
  `git archive` test run catches copied-in uncommitted files.
- **Record the failing-test baseline before dispatch**; integration keeps a branch only if it adds no
  new failures.
- **Rejected-branch salvage**: re-do verified-safe parts by hand at integration rather than
  cherry-picking a poisoned commit.

## B5. Judge / fix loops on visual output

- **Streetscene renders** — a 4-round "ultracode" vision judge/fix workflow scored the procedural
  street-scene SVG against a reference photograph and fed fixes back. Content match was achieved;
  style scores plateaued around 6/10 on nits.
- **QR posters** — poster-replication judges scored successive rounds **87 → 78 → 80**. Recorded
  lesson: **judges oscillate round to round; pick the best round by eye, don't trust the last score.**

## B6. Claude as a *pipeline component*, not a coding agent

`tools/streetscene/classify_proposals.py` (branch `feature/proposal-streetscene`) uses
`claude-opus-4-8` with **structured outputs** to map a crowdsourced proposal label + its real OSM
street context onto a **closed 18-action vocabulary** (`ACTIONS.md` / `actions_schema.json`), driving
a procedural street renderer. Guardrails in the system prompt: choose only from the enum, never
invent actions; use the street's current state (don't add a bike lane that exists — unless the
proposal implies stronger protection); route-kind proposals are corridor-level, point-kind are
intersection treatments; and a `renderable:false` escape hatch for labels with no physical depiction
("love this block").

An `--offline` keyword-heuristic mode runs the whole pipeline without credentials, with results
**explicitly labeled as heuristic** — which is what actually shipped the demo, since there's no
`ANTHROPIC_API_KEY` on the machine. (Note: `strip_numeric_constraints` exists because structured
outputs reject `minimum`/`maximum` — those are validated locally instead.)

## B7. Agents driving the real app to verify their own work

**`.claude/skills/verify` — the Claude-in-Chrome loop.** Named debug tabs
(`http://localhost:3000/m/<slug>?tab=<name>` → tags the title `[dbg:<name>]` and turns on every debug
channel), console reads filtered by `\[(topo|votes|cast|store|blocks|proposals|maplibre|ws)\]`,
`window.cityedit.dumpState()` as a one-call health check, screenshot→DOM coordinate scaling
(`scale = screenshotWidth / window.innerWidth`), and a list of useful repro map states. Plus the
traps:

- `javascript_tool` responses get **blocked** if the returned string pattern-matches cookies/query
  strings (className dumps, `x,y | x,y`) — return JSON arrays of numbers instead.
- Cluster fan-out ("explode") is **transient** (snaps back after 2.2 s) — probe state in the *same
  batch* as the click, no waits between.
- **A hidden/occluded Chrome window freezes rAF**, so MapLibre never fires `load`
  (`maplibreLoaded: false`, no heatmap). Bring the window forward before concluding rendering is
  broken.

**Headless puppeteer harness** — used when reading CSS wasn't enough and the layout had to be
*measured*: puppeteer-core + Chrome for Testing (`/opt/homebrew/bin/chromium` is a broken cask
wrapper). Hard-won details:

- **Chrome-for-Testing 148 headless is frame-dead on this machine** — rAF never ticks even on
  about:blank, screenshots time out, MapLibre never loads. **Pin the cached `mac_arm-138` build**, and
  check with a 1-second rAF counter before trusting any render-dependent wait.
- **Don't test clicks via in-tab synthetic MouseEvents in a hidden window**: intensive timer
  throttling freezes the app's `setTimeout`-based commits, so pins/Clear silently no-op while pure
  resolver probes keep answering — producing plausible-looking "mismatches" whose pinned value is
  frozen at a stale target. Use trusted `page.mouse.click` + a **page reload per trial** (state leaks
  through Clear-button loops), and poll with `waitForFunction`, never fixed sleeps.
- Driving a React **controlled** input needs the native-setter trick:
  `Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(inp, text)` +
  `dispatchEvent(new Event('input',{bubbles:true}))`.
- Sanity gate: the mode/map switcher is DB-backed, so if local Postgres is down `/api/maps` returns 0
  maps and any test depending on map rows silently no-ops — not a code bug.

## B8. Multi-agent operational hygiene

Several Claude sessions run on this repo concurrently, which produced its own rules:

- **Explicit `git add` paths only** (verify with `git diff --cached --name-only`) so another
  session's in-flight edits are never swept into your commit; expect other sessions to sweep *your*
  files into *theirs*.
- **Never edit `changelog/build_report.py` in place** — copy → redirect → restore.
- **The prod-tunnel port trap**, which "caused real cross-agent confusion": local dev's own DB is on
  `localhost:5432`, so a Cloud SQL tunnel bound to `-L 5432:…` **shadows it and any host-Flask agent
  silently dials prod.** Bind 5433, always.
- Surgical overlay deploys (`Dockerfile.blocks-artifacts-overlay`, digest-pinned base + artifacts
  only) exist partly so one agent can ship data artifacts **while another agent's code is in flight**.

---

# The through-line

**The simulated-agent work escalated deliberately:** behavior-realistic agents (Locust) → correctness
under concurrency (verify_loadtest / the in-process twin) → find the ceiling (tiered 5→160) → fix it
→ turn the fleet into a *budgeted pass/fail gate* (swarm_interactions). That last step is the one
that changed how deploys work — a multi-tenant swarm with per-interaction p95 budgets and a real exit
code, run against prod across successive revisions until every budget passes.

**The LLM-agent work followed the same arc toward verification:** ad-hoc subagents → worktree
isolation → dependency-graphed delegation waves → a fully unattended worker/reviewer/integrator swarm
gated on a pre-recorded test baseline. And the swarm's most durable output wasn't a feature — it was
a failure mode: **9 of 13 workers confidently verified against a stale tree, and only the adversarial
review layer caught it.**

Both halves converged on the same lesson from opposite directions: *the harness must know where it
stops measuring the thing it claims to measure.* The swarm's bandwidth-aware topology budget and the
merge-base-first review rule are the same insight, one about downlinks and one about git.
