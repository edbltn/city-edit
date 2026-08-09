#!/usr/bin/env python3
"""Post-bake gate: did every block survive into the low-zoom tiles?

blocks.pmtiles is not a decorative basemap layer — it is the heatmap's
geometry, addressed by feature id. A block tippecanoe thins out of a tile
cannot be lit by `setFeatureState`, and tippecanoe thins in proportion to
DENSITY, so the blocks it drops are the downtown ones, i.e. exactly where the
votes are. That failure is invisible at the default view (the tiles are
complete from z13 up) and only shows when you zoom out to the city, which is
how it survived unnoticed: Manhattan was losing 45% of its blocks at z12 and
83% at z11.

So the bake now checks itself. Distinct feature ids per zoom, compared with
the block count the builder just wrote, over the zooms the client actually
requests (leaflet zoom = MapLibre zoom + 1, and the lowest min_zoom in
cities.py is 10, so ml z9 is the widest tile ever fetched). The widest zoom
gets a warning rather than a failure — a whole metro at 1px cannot hold every
block inside one tile budget, and there the thinning is cosmetic.

    python verify_blocks_tiles.py <archive.pmtiles> --expect <n_blocks>
"""

import argparse
import collections
import gzip
import math
import sys

import mapbox_vector_tile as mvt
from pmtiles.reader import MmapSource, Reader, all_tiles

# Zooms at or above this must be essentially complete; below it (the widest
# view the client can reach) a shortfall is reported but tolerated.
DEFAULT_MIN_COMPLETE_ZOOM = 10
DEFAULT_THRESHOLD = 0.95

# Scanning every zoom would decode the whole archive (minutes for NYC's 4,600
# maxzoom tiles) to confirm what is never in doubt: the high zooms hold
# everything, because their tiles are a fraction of the size budget. Only the
# wide zooms can drop, so only they are read.
MAX_SCANNED_ZOOM = 13


def scan(path: str) -> tuple[dict[int, set], dict[int, int], dict[int, int]]:
    """(ids, tile count, max tile bytes) per zoom, for the low zooms only."""
    src = MmapSource(open(path, "rb"))
    ids: dict[int, set] = collections.defaultdict(set)
    tiles: collections.Counter = collections.Counter()
    biggest: collections.Counter = collections.Counter()
    for (z, x, y), data in all_tiles(src):
        if z > MAX_SCANNED_ZOOM or not data:
            continue
        tiles[z] += 1
        biggest[z] = max(biggest[z], len(data))
        raw = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data
        for layer in mvt.decode(raw).values():
            for feature in layer["features"]:
                ids[z].add(feature.get("id"))
    return ids, tiles, biggest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("archive")
    ap.add_argument("--expect", type=int, required=True,
                    help="block count from the builder (edge_blocks_<net>.json n_blocks)")
    ap.add_argument("--min-complete-zoom", type=int, default=DEFAULT_MIN_COMPLETE_ZOOM)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = ap.parse_args()

    header = Reader(MmapSource(open(args.archive, "rb"))).header()
    ids, tiles, biggest = scan(args.archive)

    print(f"[verify] {args.archive} zooms {header['min_zoom']}-{header['max_zoom']}, "
          f"{args.expect} blocks expected")
    failures = []
    for z in sorted(ids):
        share = len(ids[z]) / args.expect if args.expect else 0.0
        # Leaflet is one zoom ahead of MapLibre (256 vs 512px tiles), and every
        # zoom in the UI, the URL and cities.py is a Leaflet zoom — print both
        # so a shortfall here names the zoom you would reproduce it at.
        line = (f"[verify]   z{z:<2d} (leaflet {z + 1:<2d}): {len(ids[z]):7d} blocks "
                f"({share * 100:5.1f}%)  {tiles[z]:5d} tiles, largest "
                f"{math.ceil(biggest[z] / 1024)}KB")
        if share >= args.threshold:
            print(line)
        elif z < args.min_complete_zoom:
            print(f"{line}  — thinned (widest view, tolerated)")
        else:
            print(f"{line}  — TOO FEW")
            failures.append(z)

    if failures:
        sys.stdout.flush()  # or the stderr verdict lands above its own table
        zooms = ", ".join(f"z{z}" for z in failures)
        print(f"[verify] FAILED: {zooms} dropped more than "
              f"{(1 - args.threshold) * 100:.0f}% of the blocks. The heatmap will "
              f"have holes there — see the tippecanoe flags in build_city_blocks.sh "
              f"(--include=block_id, --no-tiny-polygon-reduction, "
              f"--maximum-tile-bytes).", file=sys.stderr)
        return 1
    print("[verify] OK — every served zoom carries the block set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
