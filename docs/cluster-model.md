# The Cluster model — points, routes, and blocks as one abstraction

> **Status:** design spec (2026-06-18). This is the contract the "unify voting"
> workstream builds against. It is the UX/selection half of the vote system;
> the storage/propagation/display half is **[`vote-system-design.md`](vote-system-design.md)**.

## 0. The one idea

> **A `Cluster` is a named, ordered subset of graph edges (and the nodes they
> touch).** Points, route-proposals, and blocks are all clusters — they differ
> only in *how* the subset is chosen, not in how it is selected, highlighted,
> summarized, or voted. Every hover/select/drag/vote path goes through the
> `Cluster` interface so the three kinds behave identically.

This replaces three parallel, half-overlapping code paths (point top-proposals,
route top-proposals, ad-hoc edge/node selection) with one. The driving
observation behind the four issues that motivated this work:

1. **Selecting anything resolves to a block.** Clicking an edge or node should
   highlight the *block* it belongs to and summarize the *whole block's* votes —
   the clicked edge is only the **routing anchor**, not the unit of meaning.
2. **Route proposals must select/hover/drag exactly like point proposals.** Same
   highlight, same modal, same drag affordance — only the chosen edge subset and
   the marker shape (diamond vs square) differ.
3. **Voting a cluster votes the whole cluster.** A click records on the anchor
   edge *and* propagates across every edge of every block in the cluster
   (`vote-system-design.md` §2.1). The anchor is "secret plumbing" for the
   clustering algorithm; the user thinks in blocks.
4. **A route-proposal dropped as a ghost waypoint uses its own path.** It
   contributes its two anchors as waypoints but the leg between them is the
   proposal's stored edge path *verbatim* — not a shortest-path recompute.

## 1. The abstraction

### 1.1 Common shape (the contract)

Every cluster, regardless of kind, exposes:

| Field | Meaning |
|---|---|
| `id` | stable identity (hash of kind + edge set) |
| `kind` | `"point" \| "route" \| "block"` |
| `label` / `legendIdx` | the vote type this cluster is *about* (a point/route is per-type; a block can summarize many types) |
| `anchorEdgeId` | the single edge selection & routing snap to (the "secret" vote edge) |
| `anchorCoords` | lat/lng to drop the marker / waypoint(s) at |
| `blocks` | ordered list of blocks, each a list of edge ids (`number[][]`) |
| `blockEdgeIds` | flat union of every edge in every block — **the highlight set AND the vote set** |
| `score` / `count` | net support driving heat + ranking |

Derived, not stored: the **node set** (endpoints of `blockEdgeIds`), used for
node-level highlight and node-vote rederivation.

### 1.2 The three kinds

```
Cluster (abstract: id, kind, label, anchorEdgeId, blocks, blockEdgeIds, ...)
 ├─ PointCluster   — blocks = [ block_of(anchorEdgeId) ]; one anchor; marker = square
 │                   (today's "point top proposal" AND a bare edge/node selection)
 ├─ RouteCluster   — blocks = ordered corridor; two anchors (endpoints); marker = diamond
 │                   carries a verbatim `pathEdgeIds` (ordered) for issue-4 routing
 └─ BlockCluster   — blocks = [ one block ]; anchor = block's representative edge; marker = square
                     (the polygon the heatmap colors; what a hover/select resolves to)
```

Note **PointCluster and BlockCluster converge**: a point selection *is* a block
selection whose anchor happens to be the clicked edge. The distinction is only
provenance (a server-ranked top proposal vs. a user's free click) and marker
styling. Implementations should share everything below the `anchorEdgeId`.

### 1.3 Wire format (server → client)

Already established by `route_proposals.RouteProposal.to_dict()` — generalize it
so all three kinds share keys (snake_case on the wire):

```jsonc
{
  "id": "…", "kind": "route", "label": "Bike Lane", "legendIdx": 3,
  "anchor_edge_id": 12345,            // point/block: the one edge; route: omit or = path[0]
  "blocks": [[12,13],[20],[21,22]],   // ordered for routes
  "block_edge_ids": [12,13,20,21,22], // union — highlight + vote set
  "anchors": [n0, n1],                // node indices (route: 2; point/block: 1 repeated)
  "anchor_coords": [[lat,lng],[lat,lng]],
  "path_edge_ids": [12,20,21],        // ROUTE ONLY: verbatim leg for issue 4
  "score": 87
}
```

## 2. The shared UX contract (frontend)

All of these operate on a `Cluster` and must be **identical across kinds** (issue
1 & 2). Differences are isolated to `marker shape` and `anchor count`.

- **Hover** → highlight `blockEdgeIds` (the block polygons) + a subtle anchor-edge
  emphasis. Show the hover card with the cluster's top label.
- **Select** (click) → same highlight, *persisted*; open the **modal** (§3).
- **Drag onto / ghost-drop** → for point/block: place a waypoint at `anchorCoords`
  (snap vote to `anchorEdgeId`). For route: issue-4 behavior (§4).
- **Vote** (modal ± or banner cast) → `castVotes(blockEdgeIds, label, dir)`.
  Server propagates across blocks; client applies optimistically over
  `blockEdgeIds`.
- **Marker** → `square` for point/block, `diamond` for route
  (`proposalShapeClass`, already implemented).

Frontend touch-points to route through `Cluster`:
`GraphLayer.tsx` (resolveSelection / hover / indicator rendering / spread),
`topProposals.ts` (emit `PointCluster[]`), `routeProposals.ts` (emit
`RouteCluster[]`), `MapView.tsx` (waypoint placement, drag, drop), the modal
component, and `selection/*` (a selected cluster is part of the canonical model).

## 3. The block-aware modal summary

When any cluster is selected, the modal summarizes votes over **all edges in all
its blocks**, deduplicated per `(block, vote_type, device)`:

- `net(type) = #distinct-up-devices − #distinct-down-devices` across the block(s).
- Source: the `bd:`/`bagg:` block structures (`vote-system-design.md` §2.3),
  served via `/api/graph-votes` (or `/api/block-votes`) as `block_vote_types`.
- For a multi-block `RouteCluster`, sum the per-block deduped counts (a device on
  two blocks of the same corridor legitimately counts twice — once per block).

This is the single most visible behavior change: today's modal counts the one
edge; tomorrow's counts the deduped block.

## 4. Issue 4 — route proposal as a ghost waypoint

Dropping a `RouteCluster` into the route:

```
existing waypoints W = [start, …, end]
order (A,B) = chooseAnchorOrder(W, anchorCoords)   // minimize total duration
insert A,B as ghost waypoints
leg(A → B) = proposal.path_edge_ids   VERBATIM    // NOT OSRM / shortest path
all other legs route normally (OSRM)
```

The verbatim leg means the displayed/voted route follows the *proposal's* path
(which is a high-vote corridor, deliberately not the fastest). Routing only
recomputes the A↔B leg if the user later drags an anchor (out of scope for the
first cut unless cheap). `chooseAnchorOrder` already exists in `routeProposals.ts`.

## 5. Backend alignment

- `route_proposals.RouteProposal` → becomes (or subclasses) a shared `Cluster`
  dataclass; `PointCluster`/`BlockCluster` join it. `to_dict()` emits the §1.3
  shape including `kind` and `path_edge_ids`.
- `edge_block_id : int32[n_edges]` (the mapping in `vote-system-design.md` §2.2)
  is the keystone — it must be **built, baked next to the graph, and loaded** so
  `block_of(edge)` is O(1) everywhere (proposals, propagation, modal, display).
  Today it's referenced (`getattr(rmap.graph, "edge_block_id", None)`) but not
  populated; wiring it is the first foundational task.
- The `/api/vote` path expands incoming `edge_ids` to their `block_edge_ids`
  before the write loop (propagation), inside the existing lock.

## 6. Invariants (for tests)

- `blockEdgeIds == flatten(blocks)`, deduped, for every cluster.
- A `PointCluster` and a `BlockCluster` over the same edge produce the **same**
  highlight set and the **same** modal summary.
- Selecting a cluster then voting it casts on exactly `blockEdgeIds`.
- A `RouteCluster` dropped as a ghost yields a route whose A→B leg edge sequence
  equals `path_edge_ids`.
- Hover/select/drag handlers contain **no `kind`-specific branches** except marker
  shape and anchor count.
