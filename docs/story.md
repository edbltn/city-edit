# How City Edit Got Here

A narrative history of the app, its concepts, and the decisions that shaped them.
Every date and claim below is traceable to a commit, a design doc, or a changelog
report in this repo. Written to be quarried for messaging — talks, posts, grant
copy, pitch decks — not to be read start to finish.

**The arc in one sentence:** City Edit started as a way to watch where people
*walk*, and became a way for a city to *argue with itself about what to build* —
and almost every hard problem in between was the same problem in a new costume:
**at what grain does a vote mean something?**

---

## The four concepts, and why they exist

Before the timeline, the four ideas the app is actually made of. Each one was
arrived at by failing at the alternative first.

### 1. A vote is on a piece of street, and it has a type

A vote is the tuple `(map, edge, vote type, device) → ±1`. Four coordinates, one
sign. That's it — it's the unique key in Postgres, the identity in the packed
integer codec, and the dedup key everywhere else.

The non-obvious part is the **type**. Early City Edit had votes with no content:
you routed somewhere and the line got hotter. That produced a heatmap of traffic,
which is a *description*. Adding a vote type ("Add bike lane", "Daylight this
corner", "This crossing is dangerous") turned it into a *proposal* — the map stopped
reporting where people go and started recording what they want. Everything
downstream — proposals, legends, the modal, the imports — is only possible because
a vote carries an argument, not just a location.

### 2. Blocks: votes are *stored* on edges but *mean* something on blocks

The graph is right for storage and wrong for meaning. A street between two
intersections is 6–20 OSM edges (roadway centerline, both sidewalks, crossing
stubs). If ten people vote on "the same street," they land on ten different edges
and the map shows ten faint marks instead of one strong one. Worse, the graph's
idea of *near* isn't a human's: the short edges crossing an intersection sit
inside the perpendicular street's footprint, so a route down an avenue would cast
a vote onto every cross street it passed — the "ladder" bug.

So: **blocks**. One polygon per street segment between intersections, plus one per
junction cluster (a real intersection is many OSM nodes — centerline, crossing
ends, sidewalk corners — that get merged into a single block). Every edge belongs
to exactly one block. Blocks are where votes get **deduplicated** (one device
counts once per block per type), where they get **displayed**, and what a hover or
click **highlights**.

The invariant that makes the whole thing coherent:

> **A block can never hold both a `+` and a `−` from the same device for the same
> vote type.**

Enforced by *block-scoped clear-then-cast*: every cast first deletes your existing
same-type votes across every block the selection touches, then writes your new
direction onto only the edges you actually selected. You can't accidentally be on
both sides of the same street.

### 3. Route proposals: aggregate votes back up into a corridor

Point-level support answers "where." It doesn't answer "what should we build,"
because infrastructure is linear — a bike lane is a corridor, not a pin. So the
client grows **corridors** out of the vote field: for each vote type, take the
subgraph of well-supported edges, localize into components, and grow a corridor
from the heaviest edge outward, taking the strongest arc off either tip.

Two constraints keep them honest:

- **A corridor earns its length with votes.** The meter budget grows with support
  (`base + k·√score`, capped), so a chain of net-1 edges can't snake for miles.
- **A corridor must stay routable.** Each extension is accepted only if the open
  segment is still a *shortest path* through the real graph; otherwise the previous
  tip is pinned as a **ghost waypoint**. At most 3 pins, so any proposal is
  reproducible as ≤ 5 route waypoints — which is what lets a shared proposal URL
  still route back into the corridor after the proposal itself has retired.

They're **derived state**: a pure deterministic function of (topology, votes),
computed on the client, never stored. Two people looking at the same map see
identical proposals with identical ids — no server round-trip, no persistence, no
randomness.

### 4. Heat is a signed argument, not a traffic count

The heatmap's meaning was rewritten three times, and the ending is the point.

- **Net votes** (up − down) — but a hotly contested street looked identical to an
  ignored one.
- **Absolute votes** (up + down) — heat as *attention*. Better, but now a street
  everyone hates glows exactly like a street everyone wants.
- **Signed top-proposal differential** (since 2026-07-23) — a block's heat is the
  up−down margin of its *best-ranked proposal*. Positive rides a warm ramp,
  negative descends into a cold arm, and **zero is invisible** — cancelled signal
  carries no heat.

That last clause is the whole philosophy in four words. The map doesn't show you
where people are. It shows you where a city has reached a conclusion.

---

## The timeline

### Act I — A map of desire paths (2025-12-27 → 2026-01-08)

The first commit is a Flask WebSocket server streaming map state to a Leaflet
client. 730 lines. The premise: people cut corners, wear paths across grass, and
take routes the official map doesn't have. Show the aggregate.

Within two weeks it had Docker, GCP, Redis, and a routing engine — and its
permanent name in infrastructure. The Cloud Run service is *still* called
`desire-path-mapper`, a fossil of the original idea living underneath the current
one.

**Messaging note:** the founding observation is legible to anyone. "A desire path
is what people do when the pavement is wrong." The app generalizes it.

### Act II — The hex era, and why grain is everything (2026-01-25 → 2026-04-26)

A React frontend arrived, and with it an **H3 hexagon heatmap**: bin votes into
hexes, render multiple resolutions, zoom between them.

Hexes fail for this problem, and the way they failed taught the rest of the app:

- **A hex is not a place.** It's a bin. Half a hex is one street, half is another;
  a vote in a hex says "somewhere around here," which is precisely the resolution
  a proposal can't be written at.
- **Identity is the hard part, not aggregation.** Jan 26 shipped IP-weighted voting
  (each IP contributes 1.0 total). Feb 14 replaced it with unique-voter counts
  deduplicated at every resolution. Feb 18 fixed hex counts reading `1` everywhere.
  The lesson — *dedupe on a stable per-device identity, at a defined grain* — is
  now `device_id` + the block invariant.
- **Sum vs. distinct-count is a product decision, not an implementation detail.**
  Feb 13: "show raw vote counts instead of fractional IP-weighted values."
  Fractional weights were unexplainable to a user.

Feb 14 also brought the first **bulk import**: Citibike ride data as votes, with
per-ride voter ids. This is when the app learned it could be seeded with reality
instead of waiting for users — a thread that runs all the way to the DOT proposal
imports six months later.

The hex layer was deleted on 2026-04-26, along with `hex_voting.py`. The same
commit added the landing page, theme routing, and the `cityedit.org` domains. The
app changed its name and its geometry in the same breath.

### Act III — The graph becomes the substrate (2026-03-20 → 2026-06-03)

**2026-03-20** is the hinge commit: a canvas graph layer rendering the actual OSM
walk network, and — quietly, in a bullet list — **vote types**. The map stops
being about traffic and starts being about proposals.

Then the substrate got rebuilt underneath it, three times:

- **2026-05-27** — Python Dijkstra routing replaced by **self-hosted OSRM** (foot
  profile, MLD). Sub-millisecond pathfinding. The same commit replaced
  coordinate-string vote keys with **packed integer keys** — the 53-bit codec,
  mirrored byte-for-byte in Python and TypeScript with a parity test.
- **2026-06-03** — the votable graph is rebuilt from *OSRM's own PBF + foot filter*,
  so routes and votes agree by OSM node id instead of by luck. `foot_profile.py`
  mirrors `foot.lua` and they're kept in sync deliberately.
- **2026-06-03** — every vote row gains a **lat/lon anchor**, so a graph rebuild
  (which shifts every edge id) can re-snap votes instead of orphaning them. Votes
  now outlive the graph they were cast on.

**Concept unlocked:** two networks, one truth. OSRM decides *how you get there*;
the Python graph decides *what you can vote on*. Keeping them provably aligned
(there's a validation script) is what makes "vote on the route you just drew"
mean anything.

The e-bike station network shipped here too — modeled as a *degenerate graph* of
self-edges, so a network of points reuses the entire edge-voting machinery
unchanged. Good example of the codebase paying dividends on a sharp primitive.

### Act IV — Maps become first-class (2026-06-01 → 2026-06-17)

A three-day burst turned a single map into a platform: a **map registry**
(`POST /api/maps`), per-map vote-type lists, per-map Redis channels, delta
broadcast over WebSocket, and multi-city graph registry + routing. Then D.C. and
Philadelphia; the legacy segment/hex vote tables were dropped for good on 06-17.

Two smaller decisions with long tails:

- **2026-06-06 — the URL is the selection.** One ordered `Selection` is the source
  of truth; start/end/mids/vote-type are all derived from it, and it round-trips
  through `?w=coords&vt=`. In-app back/forward works. Every proposal, poster, and
  QR code that came later is just a URL — because the selection model was made
  serializable first.
- **2026-06-14 — the crash that forced defense-in-depth.** A stale IndexedDB cache
  crashed mobile Safari on load. The fix wasn't one fix: the server stamps topology
  dimensions onto vote payloads, the client validates lengths and headers, and an
  error boundary clears the poisoned cache and reloads once. Cache invalidation as
  a *protocol* rather than a hope.

### Act V — Blocks: the coherence layer (2026-06-16 → 2026-08-02)

The longest and most technically stubborn thread in the project. Six weeks of
converging on "what is one place?"

- **06-16** — First attempt, NYC-only: derive blocks from the city's **planimetric**
  roadbed and sidewalk data via segment-Voronoi. High quality, but it only works
  in a city with that open data.
- **06-17** — A **city-agnostic generic generator**, benchmarked against the
  planimetric blocks as ground truth, with the buffer widths calibrated against it.
  A generalizable pipeline validated by a non-generalizable one — a pattern worth
  naming in messaging.
- **06-18** — The block-layer design ships with a central decision: on write, **fan
  a vote out to every edge in its block**. It gets reversed within weeks (below).
- **07-04** — **The reversal, and the three-layer model.** Propagation is wrong: it
  puts votes on edges the user never selected, which corrupts routing, migration,
  and any future re-aggregation. Instead: *votes stay on the selection edges;
  blocks are the aggregation, display and interaction grain.* Blocks stop being a
  storage concept and become a semantic one. This is the doc that still governs
  the app (`docs/three-layer-model.md`).
- **07-07 → 07-08** — Junction blocks. First discs at junction nodes to kill the
  "ladder," then merged junction *clusters*, then the big one: the **graph-first
  builder**. Old pipeline: draw polygons from drive centerlines, then map edges
  into them geometrically — which left edges unmapped (heatmap holes) and edges
  snapped into polygons they didn't touch. New pipeline: **decide membership first,
  topologically, for every edge; generate each polygon from its own member edges.**
  Coverage and overlap are then 100% *by construction*, not by measurement.
- **07-13 → 08-02** — Disjointness, in four more rounds: unconditional Voronoi trim
  with stranded-edge re-homing (07-13), disjoint across *all* block classes with a
  ship-frame audit (07-22), oversized corridors split at their thinnest crossing
  (07-29), and finally a contiguity guard — *a block is one place; split what
  geometry says is apart* (08-02).

**The recurring lesson, stated for reuse:** every one of these rounds is the same
correction. Geometry that is *computed from* the thing it describes holds its
invariants; geometry that is *matched against* it drifts.

### Act VI — Route proposals: from server clustering to derived state (2026-07-04 → 2026-07-30)

The first route-proposal engine ran on the server using **Leiden** community
detection. It was **deleted on 2026-07-04** and replaced with deterministic
connected-components + corridor peeling on the client.

Why that's the right trade and not a downgrade:

- **Determinism.** Leiden is stochastic; every client could see a different set of
  corridors, and none of them were reproducible in a bug report. Connected
  components + peeling gives every client byte-identical proposals.
- **The server was doing work that belonged to the viewer.** Proposals are a pure
  function of state the client already has.
- **Localization comes free.** Components localize naturally, and peeling separates
  parallel corridors *inside* a component — which is what Leiden was there for.

The refinements are a good study in tuning a derived model against reality:

| Date | Change | The failure it fixed |
|---|---|---|
| 07-09 | Recompute **minute-batched, idle-sliced** | Recomputing on every vote froze casting on the 3.3M-edge NYC bike graph |
| 07-13 | 3× longer corridors | Proposals were too short to read as infrastructure |
| 07-14 | Split loop-backs; require ≥ 5 blocks | Corridors doubled back on themselves |
| 07-15 | Per-type diversity quota (max 4) | A counter-voted Broadway swamped the list; its real sharrow/signal corridors were buried at #87 |
| 07-30 | Support floor (net > 100) + **ghost-waypoint corridors** | Noise proposals; and corridors that couldn't be re-routed from a shared link |

The 07-30 change retired both the straightness-splitting and budget-window
trimming heuristics — the routing-consistency check subsumes them. Deleting two
heuristics by finding the constraint they were both approximating.

### Act VII — Scale, and the sharp edges of a real deployment (2026-07-08 → 2026-08-02)

An agent-driven load test on 07-08 found the vote pipeline saturating at **~9
votes/sec**. What followed is a clean performance narrative:

- **Vote path** (07-09): batched Redis writes (≤3 pipelines per vote), 256 striped
  per-voter locks, debounced pre-gzipped snapshots with clients replaying deltas
  over stale bodies, one pubsub listener per process, and a load-shedding valve
  with saturation metrics.
- **Memory** (07-13): the networkx unpickle was OOM-looping on cold start. Runtime
  graphs became **compact typed arrays** (`walk_graph_arrays.npz`); the pickle is
  build-time only.
- **Wire** (07-22 → 07-23): sparse vote format, brotli topology, cacheable z/x/y
  block tiles behind an nginx cache, true stale-while-revalidate, mode-scoped
  prewarm, parallel client fetches. **Cold map load: ~9s → ~2.5s.**
- **Mobile** (06-14 → 07-23): the "a problem repeatedly occurred" Safari crash was
  the doubled NYC graph decoding to ~497 MB of *boxed* JS numbers. Typed-array
  topology fixed it; a later round had to purge pre-sparse IndexedDB entries that
  were still crash-looping already-poisoned devices.

The staging environment was built (07-23) and decommissioned (08-01) for lack of
traffic. Honest detail worth keeping: the parity work still paid for itself as a
forcing function for digest-based deploys.

### Act VIII — Off the screen and into the street (2026-07-27 → 2026-08-03)

The most recent phase is the app reaching outward.

- **07-27** — Fresh dangerous-intersection rankings computed six different ways,
  turned into **QR posters** for physical placement, each pointing at a deep-linked
  selection with an `?src=` tag so real-world placement becomes measurable. A
  poster-book PDF with per-poster codes and a placement checklist. Then a week of
  print craft: neighborhood names, low-ink dark posters, halftone night skies,
  auto-fit dark clearings.
- **07-30** — **NYC DOT's official proposals imported as votes** (60 cast to prod,
  scraped from the project site at 1 req/s, hashed per-project voter ids), on a
  weekly job. The city's own plans now sit in the same data model as a resident's
  tap — arguable, up- or down-votable, aggregated identically.
- **08-01 → 08-03** — Provenance: vote rows cite the actual source proposal behind
  them (`[a][b][c]` links). Notably, proximity-based association was **rejected** —
  provenance is per-vote, not per-neighborhood. A detour-ratio guard demotes
  corridors that hairpin (an East River geocode stranded on an esplanade produced a
  7.75 km "shortest path").
- **08-03** — **Sacramento**, added as a city in a day: graph, blocks, tiles, and
  its Public Works project index imported as 29 votes. The config-driven city
  checklist works.

---

## Design principles the history keeps re-deriving

Five things this codebase learned the hard way. These are the reusable claims.

1. **Pick the grain of meaning, then build storage under it.** Hexes, edges, and
   blocks are three answers to one question. The app works now because storage
   (edges), meaning (blocks) and argument (corridors) are separate layers instead
   of one compromise.
2. **Derive rather than store.** Route proposals, node heat, and button state are
   all computed from state that already exists. Nothing to migrate, nothing to
   desync, and the same inputs give the same answer on every device.
3. **Generate from membership, not by matching.** Deciding what belongs where
   *first*, then generating geometry from that decision, makes coverage and
   disjointness hold by construction. Six weeks of block bugs are all violations of
   this one rule.
4. **Determinism beats sophistication.** Leiden clustering was more sophisticated
   than connected components + peeling. It was also unreproducible, which made it
   worse.
5. **Reverse a decision in the docs, in writing.** The propagate-then-dedup design
   was reversed and the reversal is recorded in `docs/archive/README.md`, entry by
   entry, with what supersedes what. Cheapest institutional memory available.

---

## Ready-made lines

Phrasings that are accurate and land:

- "A desire path is what people do when the pavement is wrong. City Edit is that,
  for a whole city, with a vote attached."
- "The map doesn't show where people are. It shows where a city has reached a
  conclusion — and zero is invisible, because cancelled signal carries no heat."
- "Votes are stored on street segments but *counted* on blocks, because a street
  between two intersections is one place to a person and twenty edges to OpenStreetMap."
- "Every proposal on the map is reproducible as a route you can share, walk, and
  argue with."
- "NYC DOT's official plans and a resident's tap are the same data type. That's the
  point."
- "We deleted the community-detection engine and got better proposals — because
  every viewer now computes the same ones."

## Numbers worth citing

| | |
|---|---|
| First commit | 2025-12-27 (730 lines) |
| Commits to date | 334, across ~45 active days |
| Cities | NYC, D.C., Philadelphia, SF, Chicago, Sacramento |
| NYC graph | ~673K nodes, ~1.97M edges (5 boroughs) |
| Cold map load | ~9s → ~2.5s (2026-07-23) |
| Vote throughput | saturated at ~9/s → mitigated (2026-07-09) |
| Mobile crash cause | ~497 MB of boxed JS numbers → typed arrays |
| Route proposal reproducibility | ≤ 5 waypoints, always |
| Change-log reports | 56 HTML reports in `changelog/` |

## Where to verify any of this

- `docs/three-layer-model.md` — the governing spec (graph / blocks / proposals)
- `docs/voting-architecture.md` — vote identity, the 53-bit codec, migration anchors
- `docs/archive/README.md` — what was superseded, and by what
- `changelog/index.html` — 56 dated reports with the actual diffs
- `git log --reverse --date=short --pretty='%ad %h %s'` — the raw spine
