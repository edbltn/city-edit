#!/usr/bin/env python3
"""Compare the generic OSM blocks against Brook's planimetric ground-truth blocks.

Both are keyed by the same `seg_id` (same consolidated centerline graph), so the
comparison is a clean per-segment overlay: how well does the buffered-centerline
right-of-way approximate the planimetric roadbed+sidewalk right-of-way?

Metrics (all areas in m², UTM 18N):
  - per-segment IoU  = inter / union            (primary fidelity metric)
  - area ratio       = generic_area / brook_area
  - centroid offset  = distance between block centroids (m)
  - coverage         = Σ inter / Σ brook_area    (how much of Brook's ROW we cover)
  - spill            = Σ (generic − brook) / Σ brook_area  (excess area outside Brook)
  - count parity, and segments present in only one set

Usage:  BLOCKS_OUT=/path/to/output  CITY=nyc  python compare_blocks.py
Writes blocks_compare_<city>.json next to the inputs and prints a report.
"""
import warnings; warnings.filterwarnings("ignore")
import json, os
from pathlib import Path
import numpy as np, geopandas as gpd

OUT = Path(os.environ.get("BLOCKS_OUT", Path(__file__).resolve().parent / "output"))
CITY = os.environ.get("CITY", "nyc")


def _pct(x):
    return f"{100*x:.1f}%"


def load(path):
    g = gpd.read_file(path)
    if g.crs is None:
        g = g.set_crs(4326)
    return g.to_crs(32618)


def main():
    brook_path = OUT / f"blocks_{CITY}.geojson"
    gen_path = OUT / f"blocks_generic_{CITY}.geojson"
    if not brook_path.exists():
        raise SystemExit(f"missing ground truth: {brook_path}")
    if not gen_path.exists():
        raise SystemExit(f"missing generic blocks: {gen_path}")

    brook = load(brook_path).dissolve("seg_id").reset_index()  # one geom per seg
    gen = load(gen_path).dissolve("seg_id").reset_index()
    brook["b_area"] = brook.geometry.area
    gen["g_area"] = gen.geometry.area

    b_ids, g_ids = set(brook.seg_id), set(gen.seg_id)
    both = sorted(b_ids & g_ids)
    only_brook = b_ids - g_ids
    only_gen = g_ids - b_ids

    bi = brook.set_index("seg_id")
    gi = gen.set_index("seg_id")

    ious, ratios, cdists = [], [], []
    inter_sum = b_sum = g_sum = 0.0
    for sid in both:
        bg, gg = bi.geometry[sid], gi.geometry[sid]
        ba, ga = float(bi.b_area[sid]), float(gi.g_area[sid])
        inter = bg.intersection(gg).area
        union = bg.union(gg).area
        iou = inter / union if union > 0 else 0.0
        ious.append(iou)
        ratios.append(ga / ba if ba > 0 else np.nan)
        cdists.append(bg.centroid.distance(gg.centroid))
        inter_sum += inter; b_sum += ba; g_sum += ga

    ious = np.array(ious); ratios = np.array(ratios); cdists = np.array(cdists)

    def stats(a):
        a = a[~np.isnan(a)]
        return {
            "mean": float(np.mean(a)), "median": float(np.median(a)),
            "p10": float(np.percentile(a, 10)), "p90": float(np.percentile(a, 90)),
        }

    report = {
        "city": CITY,
        "counts": {
            "brook_segments": len(b_ids), "generic_segments": len(g_ids),
            "matched": len(both),
            "only_brook": len(only_brook), "only_generic": len(only_gen),
        },
        "iou": {
            **stats(ious),
            "frac_ge_0.50": float(np.mean(ious >= 0.50)),
            "frac_ge_0.70": float(np.mean(ious >= 0.70)),
        },
        "area_ratio_generic_over_brook": stats(ratios),
        "centroid_offset_m": stats(cdists),
        "coverage_of_brook": inter_sum / b_sum if b_sum else 0.0,
        "spill_over_brook": (g_sum - b_sum) / b_sum if b_sum else 0.0,
        "total_area_m2": {"brook": b_sum, "generic": g_sum},
    }

    dst = OUT / f"blocks_compare_{CITY}.json"
    json.dump(report, open(dst, "w"), indent=2)

    c = report["counts"]; iou = report["iou"]
    print(f"\n=== Generic vs Brook planimetric blocks — {CITY} ===")
    print(f"segments: brook={c['brook_segments']}  generic={c['generic_segments']}  "
          f"matched={c['matched']}  only_brook={c['only_brook']}  only_generic={c['only_generic']}")
    print(f"IoU   mean={iou['mean']:.3f} median={iou['median']:.3f} "
          f"p10={iou['p10']:.3f} p90={iou['p90']:.3f}  "
          f"|  ≥0.50: {_pct(iou['frac_ge_0.50'])}   ≥0.70: {_pct(iou['frac_ge_0.70'])}")
    ar = report["area_ratio_generic_over_brook"]
    print(f"area ratio (generic/brook)  median={ar['median']:.2f} "
          f"[p10={ar['p10']:.2f} p90={ar['p90']:.2f}]")
    co = report["centroid_offset_m"]
    print(f"centroid offset (m)  median={co['median']:.1f} p90={co['p90']:.1f}")
    print(f"coverage of Brook's ROW: {_pct(report['coverage_of_brook'])}   "
          f"spill beyond Brook: {_pct(report['spill_over_brook'])}")
    print(f"wrote {dst}")


if __name__ == "__main__":
    main()
