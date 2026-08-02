#!/usr/bin/env python3
"""Scan a blocks_final_<city>.geojson for blocks that are one block but two
PLACES — the defect the builder's §4c contiguity pass exists to prevent.

A block may legitimately ship as a MultiPolygon: a street corridor's tube is
cut wherever a junction cell or a grade-separated crossing takes a bite out of
it, leaving pieces a few metres apart that still read as one street. What must
never ship is a block whose pieces sit far enough apart to be different places
— voting on it would cast in both, and selecting it would light two
disconnected shapes on opposite sides of a park.

The measure is the block's SPREAD: build a minimum spanning tree over its
polygon pieces (edge weight = the gap between them) and report the widest edge.
That is exactly the gap threshold at which the block would fall apart, so
"spread <= PART_GAP_M for every block" is the shipped invariant.

Usage: python scan_contiguity.py <geojson> [<geojson> ...]
Exit status is 1 if any block exceeds --max-spread (default 20 m).
"""
import argparse
import json
import math
import sys

import numpy as np
from shapely.geometry import shape


def _pieces(geom):
    return list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]


def spread_m(geom, mlon, mlat) -> float:
    """Widest edge of the MST over the block's pieces, in metres."""
    parts = _pieces(geom)
    if len(parts) < 2:
        return 0.0
    scale = (mlon + mlat) / 2          # degrees → metres, isotropic enough
    reached, remaining, widest = [0], list(range(1, len(parts))), 0.0
    while remaining:
        gap, nxt = min((parts[a].distance(parts[b]) * scale, b)
                       for a in reached for b in remaining)
        widest = max(widest, gap)
        reached.append(nxt)
        remaining.remove(nxt)
    return widest


def scan(path: str, max_spread: float) -> int:
    with open(path) as fh:
        feats = json.load(fh)["features"]
    lat0 = shape(feats[0]["geometry"]).centroid.y
    mlat, mlon = 110574.0, 111320.0 * math.cos(math.radians(lat0))

    spreads, multi, offenders = [], 0, []
    for ft in feats:
        geom = shape(ft["geometry"])
        if geom.geom_type == "MultiPolygon":
            multi += 1
        sp = spread_m(geom, mlon, mlat)
        spreads.append(sp)
        if sp > max_spread:
            p = ft["properties"]
            c = geom.centroid
            offenders.append((sp, len(_pieces(geom)), p.get("block_id"),
                              p.get("n_edges"), p.get("road_name"),
                              round(c.y, 5), round(c.x, 5)))

    spreads = np.array(spreads)
    name = path.split("blocks_final_")[-1].replace(".geojson", "")
    print(f"\n=== {name}: {len(feats)} blocks, {multi} multi-piece "
          f"({100 * multi / len(feats):.1f}%) ===")
    print("  spread percentiles (m): " + "  ".join(
        f"p{q}={np.percentile(spreads, q):.1f}" for q in (50, 90, 99, 100)))
    for t in (10, 20, 30, 60, 150):
        print(f"    over {t:3d} m apart: {int((spreads > t).sum()):6d}")
    if not offenders:
        print(f"  OK — no block spreads wider than {max_spread:.0f} m")
        return 0
    print(f"  FAIL — {len(offenders)} blocks spread wider than {max_spread:.0f} m")
    offenders.sort(reverse=True)
    for sp, n, bid, ne, nm, lat, lon in offenders[:15]:
        print(f"    {sp:8.0f} m  pieces={n:3d} block={bid} edges={ne} "
              f"at {lat},{lon}  {nm}")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson", nargs="+")
    ap.add_argument("--max-spread", type=float, default=20.0,
                    help="metres; must match the builder's PART_GAP_M")
    args = ap.parse_args()
    sys.exit(max(scan(p, args.max_spread) for p in args.geojson))


if __name__ == "__main__":
    main()
