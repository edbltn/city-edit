---
title: Block identification
description: How an OSM edge/node graph becomes the discrete, clickable street blocks you vote on.
sources:
  - path: server/streetscape_blocks/build_blocks_graph_first.py
    anchors: [main, UnionFind, split_oversized, corr_geom, strays, tube_of, halfplane, merge_corridor, _thinnest_cut, _principal_frame, contiguous_buckets, _polyparts, _clean]
  - path: server/block_votes.py
    anchors: [pack_block_field, apply_block_delta, build_block_arrays]
---

# Block identification

## Why it exists

The votable graph is an OSM-derived mess: ~673 000 nodes and ~1.97 million edges
for NYC, where one "street" is three parallel ways (roadway plus two sidewalks),
an intersection is a cloud of a dozen nodes, and a driveway is
indistinguishable from a road. You cannot ask a person to click that. You also
cannot ask them to click a *rendered* street, because the thing that holds a
vote is an edge id.

Blocks are the bridge: **one polygon per place a person would name**. They are
the interaction and aggregation grain of the whole product — hover, click,
highlight, heat, per-block dedup, corridor grouping — while edges remain the
storage grain. See [three-layer-model.md](../three-layer-model.md) §2 for why the
split exists at all; this dossier is *how the polygons get made*.

The build is **graph-first**: membership is decided topologically, and geometry
is derived from membership afterwards. The earlier generation went the other way
(buffer the centrelines, then partition the surface by nearest segment), and
inherited every pathology of the geometry — blocks that fell apart when a buffer
did, votes that landed in whichever polygon happened to win a Voronoi cell.

## Inputs and outputs

| | |
|---|---|
| **In** | `walk_graph_arrays.npz` for one city+network — node coords, `edge_ends`, plus per-edge road class and name from the same provider output the topology etag is built from |
| **Out** | `edge_blocks_<network>.npy` — an `int32` array, one entry per edge, holding its **1-based** block id (0 = unmapped) |
| **Out** | `blocks_final_<city>.geojson` — one polygon per block, tiled into `blocks.pmtiles` for MapLibre |
| **Out** | `meta` — `topology_etag`, block/edge counts, audit percentages, the rule-A gap histogram |

The edge order **is** the topology etag's order. If the graph is rebuilt, edge ids
shift and the mapping must be rebaked — a graph deploy without a block rebake
silently rehomes every vote (see [resnap-on-deploy](../gcp-deployment.md)).

## Pseudocode

```
build_blocks(city, network):

  ── 1. Junction clusters ────────────────────────────────────────────
  junction_nodes = nodes with undirected degree >= 3
  union-find junction nodes within LINK_M of each other        # kd-tree pairs
  split_oversized(cluster)  while its extent > MAX_EXTENT_M    # two crossings
                                                               # 20m apart are
                                                               # not one place

  ── 2. Edge grouping — topological, total ───────────────────────────
  for each edge:
      both endpoints in the SAME cluster        -> captured by that cluster
      endpoints in DIFFERENT clusters,
        and length <= NODE_CAPTURE_LEN_M        -> captured by the nearer
                                                   cluster   (a crosswalk stub)
      otherwise                                 -> a corridor edge

  corridors = connected components of the corridor edges,
              linked ONLY through non-junction endpoints
              (an edge with two junction ends is its own singleton)

  ── 3. Degeneracy + equivalence fixpoint ────────────────────────────
  repeat until nothing changes (MAX_ROUNDS is a backstop):
      A  corridors sharing the SAME TWO endpoint clusters are CANDIDATES
         to merge — but sharing endpoints is not enough (a park loop's two
         arms share theirs too). Resolve anchor-first:
             anchor = the group's LONGEST corridor (the roadway, if any)
             claim c  iff  min(strays(c, anchor), strays(anchor, c))
                             <= PARALLEL_MAX_SEP_M
             whatever is left re-anchors and forms its own block
      V1 a driveway-class corridor <= STUB_MAX_M touching exactly ONE
         junction melts into that junction
      V2 a cluster touching <= 1 live corridor is not a junction —
         dissolve it into that corridor
      B  clusters with the SAME incident-corridor set (>= 2) merge
         (one intersection that step 1 split into sub-clusters)
      # A and B feed each other, hence the loop: merging sidewalk pairs
      # makes two corner clusters' corridor sets equal; merging those
      # clusters keys more corridors onto the same endpoint pair.

  ── 4. Geometry from membership ─────────────────────────────────────
  corridor polygon = union of its member edges' tubes
                     (per-class HALF_WIDTH, round caps and joins)
  junction cell    = convex hull of member nodes (+ NODE_R_M pad)
                     UNION the captured edges' tubes
                     # a cluster that captured no edges gets NO cell:
                     # it could never hold or display a vote

  Voronoi trim (junction vs junction): where two cells overlap, cut each
      at the perpendicular bisector of the clusters' centroids.
      UNCONDITIONAL — a captured edge stranded by a cut is re-homed
      afterwards. A cell left with < 1 m² of its own is absorbed.
      Sweep to a fixpoint.

  Disjointness (corridor vs corridor): each overlap region is CLAIMED by
      whichever corridor carries more member length inside it, and CUT
      from the other. A loser left with < 1 m² MERGES into the winner.
      Cuts only shrink and merges only union, so this converges.

  ── 4b. Oversized split (thinnest cut) ──────────────────────────────
  while a corridor's bbox diagonal > SPLIT_MAX_EXTENT_M and depth < SPLIT_MAX_DEPTH:
      find the principal axis (eigenvector of the centred point scatter)
      sample SPLIT_STATIONS stations across SPLIT_BAND of that axis
      width(station) = length of the polygon ∩ the perpendicular chord
                       # a GAP between parts measures 0 and splits free
      cut at the narrowest station; members follow their midpoints
      recurse on both halves

  ── 5. Contiguity — the shipped geometry has the last word ──────────
  bucket each block's polygon PARTS so every part is within PART_GAP_M
      of another part in its bucket; emit ONE BLOCK PER BUCKET
  members follow their own geometry (midpoint's bucket, else the bucket
      its line crosses, else nearest); a bucket holding no member is dropped

  ── 6/7. Emit + audit ───────────────────────────────────────────────
  renumber to dense 1-based ids; write edge_blocks.npy + geojson + meta
  verify: coverage (edges mapped) and that each member edge TOUCHES its
      own polygon; overlap is disjoint by construction — audited anyway
```

The whole design turns on one idea: **membership is topological, geometry is
derived, and where they disagree the geometry gets the last word** (§5). Every
class of bug this file has shipped came from letting a geometric accident decide
a *membership* question, or from trusting membership after geometry had cut it
apart.

## Tuning knobs

Every knob below is overridable by an environment variable of the same name
(except `MAX_ROUNDS`, whose variable is `MERGE_MAX_ROUNDS`) — the tabulated value
is the baked-in default that ships.

| Knob | Value | Defined in | What breaks if you change it |
|---|---|---|---|
| `LINK_M` | `18` | `build_blocks_graph_first.py` | Junction-clustering link radius. Raise it and two nearby crossings fuse into one unclickable super-junction; lower it and one intersection splits into corner blobs (rule B then has to clean up after you). |
| `MAX_EXTENT_M` | `40` | `build_blocks_graph_first.py` | Cap on a junction cluster's extent before `split_oversized` cuts it. This is the backstop that stops `LINK_M` chaining across a boulevard. |
| `NODE_R_M` | `8` | `build_blocks_graph_first.py` | Pad around a junction's convex hull. Kept in proportion to the 4 m footway tube half-width; 10 read as chunky gloops on the Central Park test map. |
| `NODE_CAPTURE_LEN_M` | `30` | `build_blocks_graph_first.py` | Longest edge a junction may swallow as a crosswalk stub. Too high and real short blocks vanish into intersections. |
| `STUB_MAX_M` | `25` | `build_blocks_graph_first.py` | Longest driveway-class dead-end that melts into its junction (rule V1). |
| `MAX_ROUNDS` | `25` | `build_blocks_graph_first.py` | Backstop on the §3 fixpoint. Each rule strictly shrinks a count, so termination is guaranteed; hitting this means a rule stopped shrinking and you have a bug. |
| `PARALLEL_MAX_SEP_M` | `30` | `build_blocks_graph_first.py` | How far a corridor may stray from its group's anchor and still count as the same street. Measured against the **longest** member, so the bound is half a right-of-way, not a whole one. 30 clears Park Avenue (~43 m building line to building line) while rejecting the East River Park arms at 72 m. Raise it and one click votes in two places. |
| `HUG_SAMPLE_M` | `5.0` | `build_blocks_graph_first.py` | Densify step for the stray-distance probe. Coarser and a long straight edge hides its own middle from the measurement. |
| `SPLIT_MAX_EXTENT_M` | `400` | `build_blocks_graph_first.py` | Above this bbox diagonal a corridor is cut in two. ≈ p99.5 of the NYC corridor-extent distribution (a long Manhattan block face is ~280 m), so ordinary streets are untouched. |
| `SPLIT_BAND` | `(0.35, 0.65)` | `build_blocks_graph_first.py` | Where along the principal axis cut stations are sampled. Widen it and cuts drift toward the ends, shaving slivers instead of halving. |
| `SPLIT_STATIONS` | `13` | `build_blocks_graph_first.py` | Stations sampled inside the band. More is slower and rarely finds a better cut. |
| `SPLIT_MAX_DEPTH` | `8` | `build_blocks_graph_first.py` | ≤ 2⁸ pieces from one block — a backstop, not a target. |
| `PART_GAP_M` | `20` | `build_blocks_graph_first.py` | Two polygon parts further apart than this are two *places* and become two blocks. Wider than any gap a junction cell can open in a corridor, far narrower than a separation that reads as two places. |
| `DEFAULT_HALF_WIDTH` | `8.0` | `build_blocks_graph_first.py` | Tube half-width for a road class not in `HALF_WIDTH`. |
| `WIDTH_SCALE` | `1.0` | `build_blocks_graph_first.py` | Global multiplier on every tube half-width — for parameter sweeps. |

## Invariants

These hold by construction and are re-verified in the build's own audit (§7);
`scan_overlaps.py` and `scan_contiguity.py` check a shipped bake independently.

- **Total coverage.** Every non-self edge is mapped to exactly one block.
- **Disjointness.** No two block polygons overlap by ≥ 1 m². Junction-vs-junction
  is enforced by the Voronoi trim, corridor-vs-corridor by the claim-and-cut
  sweep, and §4b/§5 splits only ever subdivide an already-disjoint polygon.
- **Contiguity.** A block's polygon is one connected piece, up to `PART_GAP_M`.
- **Membership touches geometry.** A block's member edges lie on (within ~2 cm of)
  its own polygon — the audit prints this percentage and it must be ~100%.
- **1-based ids.** Block id 0 means *unmapped*, so `edge_blocks` can be a plain
  `int32` array with a falsy sentinel.
- **Etag pinning.** `meta.topology_etag` records the graph the bake was made
  against. Flask refuses a mapping stamped for a different topology and falls
  back to a zero-block map (`stamped X != live Y; ignoring`).

## Failure modes and history

| What went wrong | Why | Fix |
|---|---|---|
| One click voted in two places, lighting two ribbons 70–230 m apart | Rule A merged any two corridors sharing endpoint junctions — including a park loop's two arms and a street plus the service road that rejoins it | `PARALLEL_MAX_SEP_M` anchor-hug test ([2026-07-14](https://github.com/edbltn/city-edit/blob/main/changelog/2026-07-14-unify-cluster-explode.html)) |
| ~4 900 visibly stacked junction pairs on NYC | The Voronoi trim had a veto — "skip a cut that would strand a captured edge" — and big multi-node intersections always had one | Cuts made unconditional, stranded edges re-homed afterwards ([2026-07-13](https://github.com/edbltn/city-edit/blob/main/changelog/2026-07-13-junction-disjoint-blocks.html)) |
| The Central Park reservoir loop was ONE block, 4.3 km across | The fixpoint's merge rules had no size cap | §4b thinnest-cut split ([2026-07-29](https://github.com/edbltn/city-edit/blob/main/changelog/index.html)) |
| The whole bake crashed on some graph vintages | A zero-length member edge (two distinct node ids, identical coordinates); GEOS refuses to segmentize it | `corr_geom` contributes degenerate members as bare sample points |
| Heat vanished when you zoomed out | Not this builder — tippecanoe thinned `blocks.pmtiles` by density downstream | `--no-tiny-polygon-reduction`, gated by `verify_blocks_tiles.py` |
| Four-lobed clover shapes at street corners | Junction cells were per-node disc unions | Convex hull of member nodes instead |

## Extension points

- **Non-street networks.** Station networks (`STATION_NETWORKS`) skip most of
  this: each station is a degenerate self-edge and gets its own trivial block.
  A new network type plugs in the same way.
- **Municipal ground truth.** The builder is deliberately OSM-only so it runs for
  any city. A city with planimetric roadbed/sidewalk open data could supply real
  right-of-way polygons in §4 while leaving §1–3 (membership) untouched — that
  separation is the whole point of graph-first.
- **Per-city calibration.** `HALF_WIDTH` and `PARALLEL_MAX_SEP_M` are
  NYC-calibrated. The rule-A gap histogram is stamped into `meta` on every bake
  precisely so another city can be tuned from evidence rather than by eye.

## Downstream

`edge_blocks` is read by `server/block_votes.py` to maintain the per-block
deduped vote hashes (`pack_block_field`, `apply_block_delta`, `build_block_arrays`),
and shipped to the client inside the topology blob, where it drives hover,
selection, [heat](05-heat-coloring.md), the one-pin-per-block rule in
[02](02-top-proposals.md), and corridor grouping in [03](03-route-proposals.md).
