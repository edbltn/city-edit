# Voting architecture (unified)

This document describes the single, unified voting model after the
"too many codepaths" cleanup. It is the source of truth for how a vote is
identified, stored, cast, reconciled, and migrated.

## 1. What a vote is

A vote is the tuple:

| Component | Field | Notes |
|-----------|-------|-------|
| **A — target** | `edge_id` (graph edge index) anchored to `lat`/`lon` | Edges are the canonical target. A *node* selection votes on its representative adjacent edge; node heat is **derived** from edges (max net of adjacent edges). `lat`/`lon` is the edge midpoint — the **migration anchor** (see §6). |
| **B — user** | `device_id` (+ `ip_hash`) | `device_id = sha256(voter_id)[:16]`, where `voter_id` is a per-browser UUID in `localStorage`. Falls back to `ip_hash = sha256(ip)[:16]` when anonymous. `device_id` is the dedup key; `ip_hash` is kept for abuse/analytics. |
| **C — vote type** | `vote_type_id` (↔ `label`) | e.g. `"Add bike lane"`. Global, stable `SERIAL` id; `0` = untyped. |
| **D — map** | `map_slug` | e.g. `nyc-walkways`. The vote namespace; each map has its own city graph, so `edge_id` is only meaningful paired with a slug. |

Plus a `direction`: `+1` (up) / `-1` (down). Absence of a row = no vote (`0`).

**Canonical identity:** `(map_slug, edge_id, vote_type_id, device_id)` → `direction`.
This is the Postgres unique key (`edge_votes_identity_key`) and the only dedup key.

## 2. The codec (one bit layout, both sides)

A vote field is packed into a single integer, identical in Python and TypeScript.
45 bits, fits `Number.MAX_SAFE_INTEGER` (2^53) and Redis hash field strings.

```
bit  [44]      direction    (0 = up, 1 = down)   ← only on Redis count fields
bits [43..28]  vote_type_id (16 bits, ≤ 65 535)
bits [27..24]  mode         (4 bits — the map's mode; constant per map hash)
bits [23..0]   edge_id      (24 bits, ≤ 16 M edges)
```

- `pack(edge_id, mode, vt_id)` — the **direction-less identity** (bits 0–43). Used as
  the client "have I voted" store key (value = direction) and the Redis-field base.
- `redis_field(edge_id, mode, vt_id, direction)` — identity + the direction bit. The
  Redis hash field whose value is the running count.

Single source of truth:
- **Backend:** `server/vote_store.py` (`pack` / `unpack` / `redis_field` / `MODE_IDS`).
- **Frontend:** `client-react/src/utils/voteKey.ts` — a byte-for-byte mirror.
- A parity test asserts identical packing for a shared vector set on both sides
  (`server/tests/unit/test_vote_codec.py` ↔ `voteKey.test.ts`).

## 3. Client state (one store)

`client-react/src/utils/voteStore.ts` replaces the two former stores
(`votedSegments.ts` + `myVotes.ts`). It is keyed by the packed identity
`pack(edge_id, mode, vt_id)` → `1 | -1`, persisted to `localStorage`.

- `getVote(mode, edgeId, label)` / `setVote(...)` / `clearVote(...)` resolve the
  label→id via the loaded `vote_types` map (`setVoteTypeMap`). Unknown label → no
  vote (`0`), which is always correct for a brand-new type.
- `coverage(mode, edgeIds, label, dir)` answers the multi-select availability
  question in one pass at **block grain** (see
  [three-layer-model.md](three-layer-model.md) §4): of the blocks the edges touch,
  which already hold my `dir`, which hold the opposite, which are unvoted. Drives
  the tri-state button rendering (active only when *every* block is covered).

## 4. One cast path

Both the top-bar route cast and the in-map proposal `+`/`−` go through
`castVotes({ edgeIds, label, direction })` (RouteContext). The plan/press
semantics — block-scoped **clear-then-cast** — are defined in
[three-layer-model.md](three-layer-model.md) §4; mechanically:

1. Compute block coverage. Every touched block already at `direction` →
   **unvote** (`direction = 0`, clears my votes of that type across the touched
   blocks). Otherwise clear-then-cast `direction` on the selection edges.
2. Optimistic apply (`optimistic-vote` event → `applyMyVoteChange`), recording a
   rollback snapshot — for the cast edges *and* the locally-known cleared edges.
3. `POST /api/vote { map, edge_ids, vote_type, direction, voter_id }`.
4. Server replies (`cleared` lists edges it unvoted beyond the selection) +
   broadcasts **authoritative `vtCounts`**; the client SETs them (idempotent), so
   the optimistic guess can never drift or double-count.
5. On failure: roll back the optimistic apply and the local store entry.

## 5. Server (one endpoint)

`POST /api/vote` has a single directional codepath (block-scoped semantics per
[three-layer-model.md](three-layer-model.md) §4.2–4.3):

- Expands the selection edges to their touched blocks (`edge_block_id`) and
  clears the device's same-type rows across those blocks, inside the voter lock.
- `direction != 0`: upserts the row on each selection edge; `direction == 0`:
  clears only. Redis edge counts and block-dedup state (`block_votes`) move
  accordingly.
- Persists to Postgres **synchronously** (so the next vote reads the committed
  prior direction), then publishes `vtCounts` read straight back from Redis.
- Invariant: no block ever holds `+` and `−` from the same device for the same
  vote type.

Removed: the separate "bulk upvote" branch, the `point`/`segments` snapping
branches, `record_point_vote`, and the dead `desire_path_voting` /
`votes` / `node_votes` / `hex_votes` paths. The client always resolves a click to
an `edge_id` via the same snap path it uses for hover (`GraphSnapContext`).

## 6. Migration anchor (graph rebuilds)

Each `edge_votes` row stores the edge midpoint `lat`/`lon` (backend-derived from
the graph at write time). If the city graph is rebuilt and `edge_id` indices
shift, `resnap_votes_for_map(slug)` re-snaps every row's `lat`/`lon` to the new
nearest edge using `CityGraph.snap_point_to_edge` — the **same** nearest-edge
logic the client uses for clicks (documented duplicity: TS `voteKey`/snap ↔
Python `snap_point_to_edge`). Votes survive graph changes.
