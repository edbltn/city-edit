# Block heuristics — evaluation results

Two questions, each with a heuristic family, a metric, and a ground truth:

1. **Edge/node → block mapping.** Given graph edges, a set of block polygons, and a
   reference mapping, how *precise* is a cheap mapping heuristic?
2. **Block auto-generation.** When a city has no planimetric data to build blocks from,
   synthesise them from the routing graph and score the polygons by area error against
   the real (planimetric) blocks.

Study area: a Manhattan bbox (`-74.02,40.70 → -73.93,40.82`), 225 797 undirected walk
edges, 180 748 nodes. Ground-truth blocks: `blocks_nyc.geojson` ("brook", built from NYC
planimetric roadbed + sidewalk). All geometry compared in UTM 18N (EPSG:32618, metres).

Reproduce:
```
./env/bin/python eval/extract_edges.py        # cache graph edges/nodes for the bbox
./env/bin/python eval/eval_mapping.py         # Task 1  -> results_mapping.json
./env/bin/python eval/eval_generate.py        # Task 2  -> results_generate.json
```

---

## Task 1 — edge → block mapping precision

**Reference (gold) mapping** = exact point-in-polygon containment of the edge midpoint;
for edges in a coverage gap, nearest block within 30 m (matches the production
`block_snap_m`); else unmapped. This is the same construction the production
`edge_blocks_streets.npy` bake uses.

**Finding first — the coverage gap is real.** Even with generic blocks blanketing
Manhattan, **22.4 % of edges are not *inside* any block** (they fall in the gaps between
long thin street-segment polygons and must be snapped to the nearest one). Only 0.2 % are
beyond the 30 m snap. So a "nearest block" rule is not an edge case — it decides ~1 edge
in 5.

**Heuristic precision** (vs the gold mapping; `precision_contained` = the honest number,
measured only where containment gives unambiguous truth):

| heuristic   | what it does                                   | precision (contained) | cost (225k edges) |
|-------------|------------------------------------------------|----------------------:|------------------:|
| `H_centroid`| nearest block **centroid** (KD-tree)           | **53.1 %**            | 0.07 s            |
| `H_seg`     | nearest drive **centerline** → its block       | **71.9 %**            | 2.5 s             |
| `H_knn_poly`| 8 nearest centroids → contains-test / nearest  | **90.4 %**            | 2.8 s             |
| `H_poly`    | nearest block **polygon** (STRtree.nearest)    | **100 %** (exact)     | 4.4 s             |

**Takeaway.** Street-segment blocks are long and thin, so *centroid* distance is a bad
proxy — nearest-centroid is wrong **47 %** of the time, and even the segment-anchored
proxy misses 28 %. The correct mapping is **nearest polygon** (`STRtree.nearest`), which
subsumes containment (distance 0 for interior points) and resolves the 22 % gap edges
correctly. It costs ~20 µs/edge — trivial for an offline bake — so there is no reason to
approximate. `H_poly` *is* the reference algorithm; it is in the table as the cost
baseline, and the gap between it and the proxies (10–47 pts) is the price of cutting that
corner. Only reach for `H_knn_poly` (90 %, no full polygon index needed) if memory for the
STRtree is the constraint.

*Nodes*: an intersection node touches several blocks by definition, so a single-block
label is inherently ambiguous; the production unit is the **edge**, which is what we map.
If a node label is needed, the same `STRtree.nearest` rule applies (or the set-of-incident
-edge-blocks if a boundary is wanted).

---

## Task 2 — auto-generating blocks, scored by polygon (area) error

When no planimetric layer exists, synthesise blocks from the routing graph:
**segment-Voronoi** (partition the plane by nearest drive centerline) clipped to a
synthetic right-of-way = `buffer(W)` of the network. `W` (half street-width, m) is the
only knob. Evaluated against brook per matched `seg_id`.

Metrics: `area_rmse` (m²), `area_rmse_norm` (scale-free RMSE of relative error),
`med|relerr|` = median |A_gen/A_truth − 1|, `ratio` = mean A_gen/A_truth (bias), and
`IoU` = mean intersection-over-union (placement, not just size).

**(A) The existing `blocks_generic_nyc.geojson` vs brook** (9 107 matched Manhattan segs):
`med|relerr| = 0.131`, `ratio = 0.999` (unbiased), `IoU = 0.804`, `area_rmse = 2 339 m²`.
A solid baseline — median block area within 13 %.

**(B) Generating from scratch** (Midtown window, 279 segs). Sweeping a single global `W`,
then a per-road-class width table:

| variant          | area_rmse | norm  | med\|relerr\| | ratio | IoU   |
|------------------|----------:|------:|--------------:|------:|------:|
| fixed W=9        |     672   | 0.240 | 0.183         | 0.832 | 0.765 |
| fixed W=10       |     618   | 0.209 | 0.166         | 0.915 | 0.782 |
| fixed W=11       |     696   | 0.210 | 0.203         | 0.997 | 0.783 |
| fixed W=12       |     872   | 0.241 | 0.186         | 1.076 | 0.781 |
| class-aware ×1.0 |     555   | 0.184 | 0.171         | 0.940 | 0.807 |
| **class-aware ×1.1** | **651** | 0.193 | **0.131**   | **1.022** | **0.820** |

**Takeaways.**
- A single global width works best at **W ≈ 10–11 m** (IoU ~0.78, area within ~17 %).
  Thinner (6 m) starves the blocks (ratio 0.57); wider (≥16 m) bloats them (ratio ≥1.4).
- Brook areas are **bimodal by road class** (a parkway dwarfs a service alley), so one `W`
  can't fit both. A **per-class width table** beats every fixed width: at ×1.1 it reaches
  **IoU 0.82, unbiased (ratio 1.02), median area error 13 %** — *matching the
  planimetric-derived generic blocks with no planimetric data at all.*

**Recommendation.** Use the class-aware segment-Voronoi generator (the `CLASS_W` table in
`eval_generate.py`, scale ≈1.1) as the default block source for cities without a roadbed/
sidewalk layer; reserve the planimetric pipeline (`build_nyc_blocks.py`) for cities that
have one.

### Block shape — rectangles vs capsules (`eval_rectangles.py`, `eval_hybrid.py`)

Segment-Voronoi yields **capsule/wedge** shapes whose ends are perpendicular-bisector
cuts, not node-aligned. For node-aligned **rectangles**, buffer each segment with a flat
cap (`segment.buffer(W, cap_style="flat")`): the polygon ends exactly at the two nodes and
its sides run parallel to the edge — the edge/nodes define the boundary. Rectangles don't
tile, though, so they overlap at intersection corners. The hybrid clips each rectangle to
its Voronoi cell: straight node-aligned sides, but the bisector trims the corners so it
still tiles. Scored on the Midtown window vs brook:

| generator                | shape                    | med\|relerr\| | IoU   | overlap |
|--------------------------|--------------------------|--------------:|------:|--------:|
| Voronoi capsule (class ×1.1) | capsule, tiles       | 0.131         | 0.820 | 0.000   |
| pure rectangle ×1.0      | rectangle, node-capped   | **0.093**     | 0.740 | 0.080   |
| round-cap ×1.1           | capsule, overlaps        | 0.278         | 0.652 | 0.180   |
| **hybrid ×1.2** (rect ∩ Voronoi) | **rectangle, tiles** | 0.125     | **0.819** | **0.000** |

`overlap` = sum(block areas)/area(union) − 1; 0 means perfect tiling. Pure rectangles win
on raw area (9.3 % median) and give clean node-aligned shapes, but **8 % of the covered
area is claimed by ≥2 blocks** at intersections, so an edge near a corner maps
ambiguously. The **hybrid keeps the rectangular node-aligned sides, tiles cleanly (0 %
overlap → unambiguous mapping), and matches the capsule's placement (IoU 0.82)** at the
cost of a little area-tightness. **Recommended block shape: hybrid (rectangle ∩ Voronoi
cell), scale ≈1.2.** Clipping removes corner area, so it wants a slightly wider buffer than
the pure-rectangle (×1.0) or capsule (×1.1) optimum.

### End-to-end: mapping the generated hybrid blocks (`gen_hybrid_manhattan.py` → `eval_mapping.py`)

Generated hybrid (rectangle ∩ Voronoi) blocks over the whole Manhattan bbox (~14k
blocks, 24 s) and ran the Task 1 edge→block harness on them. Compared to the
space-filling generic blocks:

| block set                | contained | snapped(≤30m) | **unmapped** | H_centroid | H_seg | H_knn_poly | H_poly |
|--------------------------|----------:|--------------:|-------------:|-----------:|------:|-----------:|-------:|
| generic (Voronoi-buffer) |   77.6 %  |   22.2 %      |   **0.2 %**  |   53.1 %   | 71.9 %|   90.4 %   | 100 %  |
| hybrid rectangle ×1.2    |   62.0 %  |   19.4 %      |  **18.6 %**  |   58.1 %   | **98.0 %** | 96.4 %|   100 %  |
| hybrid rectangle ×1.6    |   66.4 %  |   16.3 %      |  **17.3 %**  |   58.8 %   | 98.1 %| 96.3 %|   100 %  |

Two things move in opposite directions, and the diagnostic (`diag_coverage.py`) explains why:

**Coverage collapses (0.2 % → 18.6 % unmapped).** Tight rectangles cover only the ~20–28 m
street strip. The 42 k unmapped edges sit a **median 72 m from the nearest rectangle** but
**99.8 % are inside a generic block** (distance 0). So those edges live over park paths,
plazas, and building-block interiors / cut-throughs (Central Park alone fills a big slice
of this bbox) — far from any road centerline. Widening the strip (×1.2→×1.6) barely helps
(18.6 %→17.3 %): the gap edges aren't *just outside a thin strip*, they're nowhere near a
street. This is inherent to the street-strip model, **not a width bug**.

**But cheap mapping gets *more* accurate.** `H_seg` (nearest drive centerline → its block)
jumps **72 %→98 %**, and `H_knn_poly` **90 %→96 %**. Reason: a rectangle ∩ Voronoi-cell
block *is* the nearest-centerline cell by construction, so "nearest centerline" is almost
exactly "containing block". On the loose generic blocks that proxy was much weaker.

**Interpretation / decision.** The 18.6 % is not lost data — `H_seg`/`H_poly` still assign
every one of those edges to its nearest street's block (a park-path vote lands on the
adjacent street). So the choice is a *product* decision, not an accuracy one:

- Want blocks to **visually blanket the map** (every edge sits inside a polygon, no gaps)?
  → space-filling Voronoi (generic). 99.8 % within 30 m, but blocky catchment shapes and a
  few oversized cells.
- Want **clean street-strip rectangles** (what you asked for)? → hybrid. Better shape
  (IoU 0.82, tiles, node-aligned) *and* near-exact cheap mapping (`H_seg` 98 %), but **map
  edges by nearest-centerline/polygon, never containment**, and accept that off-street
  edges (parks/plazas) bind to their nearest street rather than sitting inside a block.

Practical rule for the rectangle model: **bake `edge_block_id` with `H_poly` (or `H_seg`,
which is 98 % and needs no polygon index), not the containment+30 m-snap path** — otherwise
~1 edge in 5 reads as unmapped when it is really just beside a thin block.

### Caveats
- Task 2 (B) is a single regular-grid Midtown window; the regular grid flatters Voronoi.
  The full-Manhattan number in (A) (IoU 0.80, relerr 0.131) corroborates the window, but a
  multi-window sweep (irregular Lower Manhattan, an outer-borough grid) would harden the
  class-width table before committing it.
- `H_poly = 100 %` is exact-by-construction, not a tuned win; the real result is how far
  the cheaper proxies fall below it.
