# Vote system design — edge storage, block propagation + display

> **Status:** living design doc. Covers (1) the edge-level vote *storage* (the
> durable grain, unchanged) and (2) the **block layer** that sits on top of it:
> votes **propagate across the block at write time** and **dedup per user at
> display time**. The display dedup lives in **`server/block_votes.py`**; the
> write-time propagation lives in the `/api/vote` path; both follow this spec.
>
> The block layer is one face of the broader **`Cluster` abstraction** — points,
> routes, and blocks are all *clusters* (named subsets of graph edges/nodes) that
> share one selection / highlight / vote / display contract. See the companion
> spec **[`docs/cluster-model.md`](cluster-model.md)** for that model; this doc is
> the storage/propagation/display half of it.
>
> One-line summary: **a vote is recorded on the single anchor edge AND propagated
> to every other edge in that edge's block** (so routing/clustering see the whole
> block's support), **and the heatmap + modal dedup so each
> `(block, vote_type, direction, user)` counts at most once.** The anchor edge is
> kept verbatim too — selection and routing still snap to one edge — but the
> *vote* fans out to the block.
>
> ⚠️ **This reverses the earlier "display-projection only" framing.** Earlier
> drafts said votes stayed edge-level and blocks were a pure display projection
> with no write-path change. The decision (2026-06-18) is **propagate + dedup-
> display**: the write path DOES fan a vote out across its block, and a one-time
> backfill script makes existing `edge_votes` block-complete. §2 below reflects
> the propagate model; §1 (storage grain) is unchanged.

---

## 1. The edge-level system (today — unchanged by this work)

### 1.1 Identity

Every vote carries a **device identity** and an **IP hash**:

- `voter_id` — minted by the web client in `localStorage` (imports send the ride id).
  **Required** on every `/api/vote` (`app.py:947`); a missing id is a client bug, not
  anonymous use.
- `device_id` — `VARCHAR(16)`, derived from `voter_id` (or IP) via `_resolve_user`.
  **This is the dedup key.**
- `ip_hash` — `VARCHAR(16)`, SHA-256 of the source IP, used only for the soft
  per-IP abuse cap (`MAX_DEVICES_PER_IP_PER_EDGE`) and LRU takeover.

### 1.2 Canonical store — Postgres `edge_votes`

The durable source of truth (`database.py:226`):

```
edge_votes(
  id            SERIAL PK,
  map_slug      TEXT      NOT NULL,   -- scopes the vote to a map
  edge_id       INT       NOT NULL,   -- graph edge index (per the map's city graph)
  vote_type_id  INT       NOT NULL DEFAULT 0,
  device_id     VARCHAR(16) NOT NULL, -- the dedup key (a "user")
  ip_hash       VARCHAR(16),          -- abuse cap only
  direction     SMALLINT  NOT NULL DEFAULT 1,  -- +1 up / -1 down
  created_at, updated_at TIMESTAMP,
  lat, lon      DOUBLE PRECISION       -- migration anchor (re-snap on graph rebuild)
)
UNIQUE (map_slug, edge_id, vote_type_id, device_id)   -- "edge_votes_identity_key"
```

**Today's dedup grain is the unique key: one row per `(map_slug, edge_id,
vote_type_id, device_id)`.** A user voting the same edge+type twice updates one
row; `direction` records up/down.

### 1.3 Hot cache — Redis (the heatmap serves from here, not Postgres)

Per map, namespaced by slug:

| Key | Purpose |
|-----|---------|
| `ev:<slug>` (hash) | packed field key → **count** |
| `vote_rev:<slug>` (int) | revision; bumped on every write (`publish_delta`) |
| `vote_deltas:<slug>` (pub/sub) | live deltas to clients + cache invalidation |
| `votelock:<slug>:<device_id>` | cross-instance per-voter SET-NX lock |

The Redis field key packs **45 bits** (fits `Number.MAX_SAFE_INTEGER`,
`vote_store.py:8`):

```
bits [23..0]   edge_id      (24 bits)
bits [27..24]  mode         (4 bits — the map's mode, constant per hash)
bits [43..28]  vote_type_id (16 bits)
bit  [44]      direction    (0 = up, 1 = down)
```

`pack` / `redis_field` / `unpack` (`vote_store.py:154–177`). **Redis stores only
counts — device identity is discarded here.** That is the central fact for the
block layer (§2): you cannot deduplicate users from the Redis counts alone.

### 1.4 Write path — `/api/vote` (`app.py:926`)

```
POST /api/vote { map, mode, vote_type, voter_id, edge_ids[], direction }
  resolve_map → passcode gate → resolve edges (snap point if needed)
  vt_id = get_vote_type_id(label)               # auto-creates id on first use
  with voter_lock(redis, slug, device_id),      # cross-instance (Redis SET-NX)
       _proposal_vote_lock:                      # in-process (threading.Lock)
    for eid in edge_ids:
      prev = get_voter_edge_direction(slug, eid, vt_id, device_id)  # from Postgres
      if prev == direction: continue                                # no-op
      apply_directional(redis, slug, eid, mode, vt_id, direction, prev)  # HINCRBY
      changed.append(eid)
    # persist synchronously so the *next* vote reads the committed prior dir:
    record_edge_votes / delete_edge_votes(slug, changed, vt_id, device_id, …)
    vt_counts = read_edge_vt_counts(redis, slug, changed, mode, vt_id)  # authoritative
    publish_delta(redis, slug, changed, mode, vt_id, direction, vtCounts=vt_counts)
```

`apply_directional` (`vote_store.py:182`) handles every transition — fresh
(0→±1), reversal (±1→∓1), removal (±1→0) — by `HINCRBY +1` on the new direction's
field and `HINCRBY -1` on the prior's, in one pipeline.

`publish_delta` (`vote_store.py:205`) bumps the revision and broadcasts the
**authoritative post-write `[up, down]`** per changed edge so clients **SET** (not
increment) and can never drift.

### 1.5 Read path — `/api/graph-votes` (`app.py:1485`)

Revision-keyed, ETag-cached, single-flight (`_build_graph_votes_body`,
`app.py:315`):

```
rev = vote_rev:<slug>
cache_key = "<slug>:<mode>"                       # mode-scoped
if cached at rev: return it
with _build_lock_for(cache_key):                  # single-flight
  votes = read_all(redis, slug)                   # HGETALL ev:<slug>
  arrays = build_arrays(votes, n_edges, n_nodes, node_adj, mode_filter)
  arrays += { rev, vote_types, n_edges, n_nodes, topology_version }
```

`build_arrays` (`vote_store.py:254`) unpacks counts into:
`edge_votes[]` (net up−down per edge), `node_votes[]` (max net of adjacent edges),
`edge_vote_types[]` / `node_vote_types[]` (`[legendIdx, up, down]` triples), and a
`vote_type_legend`. Dimensions (`n_edges`/`n_nodes`/`topology_version`) are stamped
so the client can detect a topology/vote mismatch (the stale-cache guard).

### 1.6 Hydration / replay

The heatmap serves **only from Redis**. If `ev:<slug>` is empty (cold instance,
flushed Redis), `_hydrate_map_redis` (`app.py:392`) replays Postgres → Redis;
`_populate_redis` (`app.py:428`) does it for all maps at boot. **Redis is fully
derivable from Postgres at any time** — this is what makes the block layer's
derived state safe to rebuild (§2.5).

---

## 2. The block-level display layer (new — `server/block_votes.py`)

### 2.1 Goal & grain

Display votes aggregated to **blocks** (one polygon per street segment between
intersections; see `streetscape_blocks/`). Two grains, both keyed off the same
`edge_block_id` mapping (§2.2):

**Write grain — propagation.** A vote cast on any edge `E` is recorded on `E`
*and* on every other edge in `block_of(E)`, for the same
`(vote_type, direction, device)`. This is what lets the route-proposal clustering
(`route_proposals.py`, Leiden over net-positive edges) see a whole block's
support from a single click, and what makes "select one edge, vote the block"
true at the data layer. Selection and routing still snap to one **anchor** edge;
only the *vote* fans out. Propagation happens inside the existing `/api/vote`
lock (§1.4) and a backfill script (§2.8) makes historical data consistent.

**Display grain — dedup.** Even with propagation, a block's count must not
multiply by its edge count, so the heatmap dedups:

> **one count per `(map_slug, mode, block_id, vote_type_id, direction, device_id)`**

i.e. **a user's (vote_type, direction) counts once per block**, regardless of how
many edges in that block carry their vote. The **modal summary** collapses this
one step further to `(block, vote_type, device)` with
`net = #up-devices − #down-devices` — i.e. it reads the two per-direction device
sets and subtracts. (Same underlying `bd:`/`bagg:` structures; the modal just
takes `up − down`.)

> **Why both?** Propagation alone would let one user inflate a block by its edge
> count; dedup alone (the old "projection only" plan) would starve the clustering
> of block-wide support. Propagate for the algorithm, dedup for the eyes.

### 2.2 edge → block mapping

Built offline with the block geometry (see `streetscape_blocks/` + the generic
generator) and **baked next to the graph** as a dense array:

```
edge_block_id : int32[n_edges]      # edge_block_id[edge_id] = block_id, or -1 if unmapped
```

Loaded with the city graph (cached, keyed by `topology_etag + blocks_hash` so it
only rebuilds when the graph or the blocks change). Edges with `block_id == -1`
(no covering block — e.g. a stray foot edge outside any street segment) are simply
absent from the block display and remain visible in the edge layer fallback.

### 2.3 Why a **Redis-native incremental** structure (chosen tradeoff)

Block counts need **distinct devices per block**, but Redis counts (§1.3) have
already thrown identity away, and Postgres has it but we don't want a
`COUNT(DISTINCT …)` on the serve path. The chosen design keeps **serving as fast
as the edge heatmap** by maintaining the deduped counts incrementally in Redis,
accepting that this derived state must be **rebuilt on cold-start / resnap** (the
"tricky to sync" cost, called out explicitly).

**Per-block device-multiplicity hash** — one per `(slug, mode, block, vt, dir)`:

```
key:   bd:<slug>:<mode>:<block_id>:<vt_id>:<dir>     (Redis hash)
field: device_id
value: how many edges *inside this block* this device currently votes
       for this (vt, dir)
```

Then **`HLEN(bd:…) == number of distinct devices == the block's deduped count`**
for that `(vt, dir)`. The per-device multiplicity is what lets removals be exact:
a device drops out of the block only when its *last* edge in the block flips off.

**Aggregate hash** — one per `(slug, mode)`, so a serve is a single `HGETALL`:

```
key:   bagg:<slug>:<mode>          (Redis hash)
field: packed(block_id, vt_id, dir)
value: current deduped count  (== HLEN of the matching bd:… hash)
```

### 2.4 Incremental update (inside the existing vote lock)

With propagation (§2.1), a single user click marks **every edge in the block** as
changed, so `apply_block_delta` runs once per block edge. The per-device
multiplicity therefore rises to the block's edge count while `bagg` (HLEN) stays
at 1 — exactly the dedup we want. `block_votes.apply_block_delta(...)` is called
for each **changed** edge in the same `voter_lock` + pipeline as
`apply_directional`, so edge and block state move atomically per voter:

```
on a changed edge E by device D, transition prev_dir → new_dir, type vt, mode m:
  B = edge_block_id[E];  if B < 0: return            # unmapped edge: skip block layer
  if new_dir in (UP, DOWN):
      n = HINCRBY  bd:<slug>:<m>:<B>:<vt>:<new_dir>  D  +1
      if n == 1:  HINCRBY bagg:<slug>:<m>  packed(B,vt,new_dir)  +1   # D newly present
  if prev_dir in (UP, DOWN):
      n = HINCRBY  bd:<slug>:<m>:<B>:<vt>:<prev_dir>  D  -1
      if n == 0:  HDEL    bd:<slug>:<m>:<B>:<vt>:<prev_dir>  D
                  HINCRBY bagg:<slug>:<m>  packed(B,vt,prev_dir)  -1   # D fully gone
```

O(1) per changed edge. Note the asymmetry: the **aggregate** only moves on the
0↔1 device-presence boundary, which is exactly block-level dedup. Two edges in the
same block voted by the same user ⇒ `bd` field goes 1→2 but `bagg` stays at 1.

### 2.5 Serving block votes

`/api/graph-votes` (and/or a dedicated `/api/block-votes`) gains a block
projection built from one `HGETALL bagg:<slug>:<mode>`:

```
block_votes[block_id]        = up − down                       # net, for the heat ramp
block_vote_types[block_id]   = [[legendIdx, up, down], …]      # same shape as edges
n_blocks, blocks_version                                       # stale-cache guard
```

Same revision-keyed LRU + single-flight cache as the edge body. The client colors
block polygons (from `blocks.pmtiles`) by `block_votes` via the existing heat ramp;
blocks become the **primary** heat display, with edges still driving
selection/voting underneath. Cities without block artifacts fall back to the edge
heatmap unchanged.

### 2.6 Rebuild / sync — the cost we accepted

`bd:` / `bagg:` are **derived state** and must be rebuilt from Postgres whenever
their inputs change. `block_votes.rebuild_from_db(slug, mode, edge_block_id)`:

```
DELETE bd:<slug>:<mode>:*  and  bagg:<slug>:<mode>
SELECT edge_id, vote_type_id, direction, device_id
  FROM edge_votes WHERE map_slug = %s
for each row:
  B = edge_block_id[edge_id]; if B < 0: continue
  HINCRBY bd:<slug>:<mode>:<B>:<vt>:<dir> device +1     # rebuild multiplicities
then for each bd hash: HSET bagg field = HLEN(bd hash)  # derive aggregate
```

Triggered by:

1. **Cold Redis** — hook into `_hydrate_map_redis` / `_populate_redis` (§1.6):
   whenever edge votes are replayed, rebuild block state too.
2. **Graph reload / vote migration (resnap)** — `edge_id`s (and thus
   `edge_block_id`) change, so block state is stale by definition; rebuild after
   the graph swaps. (See the `graph_reload` path + `vote_migration`.)
3. **Blocks regenerated** — new `blocks_hash` ⇒ new `edge_block_id` ⇒ rebuild.

Because Postgres remains the canonical, identity-bearing store, a rebuild is
always available and authoritative. The only failure mode is *drift* if an
incremental update is missed — mitigated by always rebuilding on the triggers
above and by the fact that the aggregate is cheap to recompute (a single pass over
one map's rows).

### 2.7 Invariants (for tests)

- `bagg` field == `HLEN` of its matching `bd` hash, always.
- Σ over edges in block B of "device D votes E" ⇒ `bd[B][D] == that count`;
  `bagg` counts D once iff that count ≥ 1.
- `block_votes[B]` (net) == distinct-up-devices − distinct-down-devices in B.
- Rebuilding from Postgres yields byte-identical `bagg` to the incremental state.
- An unmapped edge (`edge_block_id == -1`) never touches `bd`/`bagg`.

---

### 2.8 Backfill — making historical votes block-complete

Pre-propagation rows have a vote on only the anchor edge of a block. A one-time
(and re-runnable) script reconciles them with the block mapping:

```
block_backfill(slug, edge_block_id):
  SELECT edge_id, vote_type_id, direction, device_id, lat, lon
    FROM edge_votes WHERE map_slug = %s
  group rows by (block_of(edge_id), vote_type_id, direction, device_id)
  for each group present on ≥1 edge:
    for every edge E in that block lacking the row:
      INSERT edge_votes(... E ...) ON CONFLICT DO NOTHING   # idempotent
  # then rebuild Redis ev: + bd:/bagg: from Postgres (§1.6, §2.6)
```

Properties: **idempotent** (the unique key `(map_slug, edge_id, vote_type_id,
device_id)` + `ON CONFLICT DO NOTHING`), **direction-aware** (an up and a down by
different users both propagate), and **anchor-preserving** (`lat`/`lon` of the
synthesized rows use the target edge's own midpoint so a later resnap stays
correct). Run it after `edge_block_id` is baked and before/with the first
propagating deploy; safe to re-run any time the block mapping changes.

## 3. Backwards compatibility & scope

- **No change** to the `edge_votes` *schema* or the `ev:<slug>` / `edge_votes[]` /
  `node_votes[]` *shapes*. What changes: the `/api/vote` write path now **fans a
  vote across its block** (§2.1), and `bd:`/`bagg:` block state is **additive
  derived state** on top. A map with no block artifacts skips propagation and
  behaves exactly as before (each edge is its own singleton block).
- **Ferries** are already excluded from the votable topology (foot profile disables
  `route=ferry`), so no ferry edges enter `edge_block_id`.
- **Per-city opt-in:** a map/city shows blocks only if it has block artifacts;
  otherwise the edge heatmap is served as before.

---

## 4. File map

| Concern | File |
|---|---|
| Bit packing, modes, vote-type cache, edge write/read, `build_arrays` | `server/vote_store.py` |
| Vote endpoint, graph-votes endpoint, hydration, prewarm | `server/app.py` |
| Postgres schema + vote persistence | `server/database.py` |
| Graph topology, `edge_midpoints`, `node_adj` | `server/graph_registry.py`, `server/python_router.py` |
| **Block dedup + rebuild + backfill** | **`server/block_votes.py`** |
| edge→block mapping + block geometry build | `server/streetscape_blocks/` |
| Unified Cluster abstraction (point/route/block) | `docs/cluster-model.md` |
| This document | `docs/vote-system-design.md` |
