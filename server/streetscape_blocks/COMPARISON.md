# Generic blocks vs Brook's planimetric ground truth

How faithfully the **city-agnostic** block generator (`build_blocks_generic.py`,
OSM only) reproduces Brook's **NYC planimetric** blocks (`build_nyc_blocks.py`,
roadbed + sidewalk open data). This is the evidence that the generic generator —
which we run for *every* city — respects the planimetric ground truth closely
enough to rely on.

## Method (`compare_blocks.py`)

Both generators partition the **same** consolidated osmnx drive graph by
segment-Voronoi, so every block carries the **same `seg_id`** — the comparison is
a clean per-segment overlay (no fuzzy matching). The only difference is the
right-of-way surface: Brook unions planimetric **roadbed + sidewalk** polygons;
the generic generator buffers the **centerlines** per road class. Per shared
segment we measure IoU, area ratio, and centroid offset (all in UTM 18N / metres);
globally we measure coverage and spill.

## Results — NYC (81,985 shared segments)

| Metric | Value |
|---|---|
| **IoU** (intersection-over-union) | **mean 0.79 · median 0.84** · p10 0.56 · p90 0.94 |
| segments with IoU ≥ 0.50 | **93.2%** |
| segments with IoU ≥ 0.70 | **78.6%** |
| **area ratio** (generic / Brook) | **median 1.00** · p10 0.73 · p90 1.35 |
| **centroid offset** | **median 1.1 m** · p90 11.5 m |
| coverage of Brook's ROW | 78.3% |
| spill beyond Brook | −8.2% (generic slightly tighter overall) |

**Interpretation.** On the segments where NYC publishes planimetric data, the
generic blocks sit on the same streets (centroid offset ~1 m), are the same size
(area ratio 1.00), and overlap strongly (median IoU 0.84). The IoU tail (worst
10% ~0.56) is dominated by wide motorway/ramp segments whose real ROW width
varies more than a per-class buffer can capture — acceptable, since a block only
needs to host the edges that fall on its street.

### Coverage is *better*, not worse

Brook's planimetric blocks cover **81,985** segments; the generic generator covers
**138,346** — it also produces blocks for the **~56k segments NYC's roadbed/
sidewalk data does not reach** (service roads, dataset gaps). `only_brook ≈ 0`
(every planimetric segment has a generic counterpart). For block-level voting,
fuller segment coverage is strictly better: more edges map to a block.

## Width calibration (what we tried)

Per-class buffer half-widths are tuned to match Brook's ROW **area**. A purely
data-driven calibration — set each class's half-width to the median of
`area / (2·length)` over Brook's blocks — **under-shot** (area ratio 0.90, IoU
0.82): the segment-Voronoi assigns intersection flare to the segments, so the
effective mid-block width must run a little wider than `area/length` implies. The
hand-tuned widths in `build_blocks_generic.py` (`HALF_WIDTH`) win and are reused
for all cities, since OSM road classes are universal.

## Reproduce

```bash
cd server/streetscape_blocks
export BLOCKS_OUT="$(pwd)/output"
./env/bin/python build_nyc_blocks.py        # ground truth (or run_all.sh)
CITY=nyc ./env/bin/python build_blocks_generic.py
CITY=nyc ./env/bin/python compare_blocks.py # writes blocks_compare_nyc.json
```
