#!/usr/bin/env python3
"""
Download OSM street network graphs for routing.

Usage:
    python download_graphs.py                    # Download default test area (small)
    python download_graphs.py --manhattan        # Download full Manhattan
    python download_graphs.py --area "40.75,40.76,-73.99,-73.98"  # Custom bbox

The graphs are saved to graph_cache/ and loaded by roam_router.py on startup.
"""

import argparse
import pickle
import time
from pathlib import Path

try:
    import osmnx as ox
    HAS_OSMNX = True
except ImportError:
    HAS_OSMNX = False
    print("Error: osmnx not installed. Run: pip install osmnx")
    exit(1)

# Cache directory
CACHE_DIR = Path(__file__).parent / "graph_cache"
CACHE_DIR.mkdir(exist_ok=True)

# Predefined areas
AREAS = {
    "test": {
        "name": "Test Area (Midtown, ~10 blocks)",
        "north": 40.760,
        "south": 40.750,
        "east": -73.980,
        "west": -73.995
    },
    "manhattan": {
        "name": "Manhattan",
        "north": 40.882,
        "south": 40.700,
        "east": -73.907,
        "west": -74.020
    },
    "central_park": {
        "name": "Central Park Area",
        "north": 40.800,
        "south": 40.765,
        "east": -73.949,
        "west": -73.981
    }
}

# Mode configurations
MODE_CONFIG = {
    "walk": {
        "network_type": "walk",
        "speed_kmh": 5.0,
    },
    "bike": {
        "network_type": "bike",
        "speed_kmh": 18.0,
    },
    "drive": {
        "network_type": "drive",
        "speed_kmh": 35.0,
    }
}


def download_graph(mode: str, bbox: dict, force: bool = False) -> bool:
    """
    Download a single mode graph for a bounding box.

    Args:
        mode: Transport mode (walk, bike, drive)
        bbox: Dict with north, south, east, west
        force: If True, download even if cache exists

    Returns:
        True if downloaded, False if skipped (already exists)
    """
    config = MODE_CONFIG[mode]
    cache_file = CACHE_DIR / f"roam_{mode}.gpickle"

    if cache_file.exists() and not force:
        print(f"  {mode}: Already exists at {cache_file}")
        return False

    print(f"  {mode}: Downloading from OSM...")
    t0 = time.time()

    try:
        G = ox.graph_from_bbox(
            bbox=(bbox["north"], bbox["south"], bbox["east"], bbox["west"]),
            network_type=config["network_type"],
            simplify=True
        )

        # Add travel time attributes
        G = ox.add_edge_speeds(G)
        G = ox.add_edge_travel_times(G)

        # Save to cache
        with open(cache_file, "wb") as f:
            pickle.dump(G, f)

        elapsed = time.time() - t0
        print(f"  {mode}: Downloaded {G.number_of_nodes()} nodes, {G.number_of_edges()} edges in {elapsed:.1f}s")
        return True

    except Exception as e:
        print(f"  {mode}: Error - {e}")
        return False


def download_all(bbox: dict, force: bool = False):
    """Download all mode graphs for a bounding box."""
    print(f"\nDownloading graphs for area:")
    print(f"  North: {bbox['north']}, South: {bbox['south']}")
    print(f"  East: {bbox['east']}, West: {bbox['west']}")
    print()

    downloaded = 0
    for mode in MODE_CONFIG:
        if download_graph(mode, bbox, force):
            downloaded += 1

    print(f"\nDone! Downloaded {downloaded} graphs to {CACHE_DIR}/")

    # Clear unified cache since individual graphs changed
    unified_cache = CACHE_DIR / "roam_unified.gpickle"
    if unified_cache.exists() and downloaded > 0:
        unified_cache.unlink()
        print("Cleared unified cache (will rebuild on next request)")


def parse_bbox(bbox_str: str) -> dict:
    """Parse 'south,north,west,east' string into bbox dict."""
    parts = [float(x.strip()) for x in bbox_str.split(",")]
    if len(parts) != 4:
        raise ValueError("Expected 4 values: south,north,west,east")
    return {
        "south": parts[0],
        "north": parts[1],
        "west": parts[2],
        "east": parts[3]
    }


def main():
    parser = argparse.ArgumentParser(description="Download OSM graphs for routing")
    parser.add_argument("--test", action="store_true", help="Download small test area (default)")
    parser.add_argument("--manhattan", action="store_true", help="Download full Manhattan")
    parser.add_argument("--central-park", action="store_true", help="Download Central Park area")
    parser.add_argument("--area", type=str, help="Custom bbox: 'south,north,west,east'")
    parser.add_argument("--force", action="store_true", help="Re-download even if exists")
    parser.add_argument("--mode", type=str, help="Download only one mode (walk/bike/drive)")

    args = parser.parse_args()

    # Determine which area to download
    if args.area:
        bbox = parse_bbox(args.area)
        print(f"Using custom area")
    elif args.manhattan:
        bbox = AREAS["manhattan"]
        print(f"Using {AREAS['manhattan']['name']}")
    elif args.central_park:
        bbox = AREAS["central_park"]
        print(f"Using {AREAS['central_park']['name']}")
    else:
        # Default to test area
        bbox = AREAS["test"]
        print(f"Using {AREAS['test']['name']} (use --manhattan for full coverage)")

    if args.mode:
        if args.mode not in MODE_CONFIG:
            print(f"Unknown mode: {args.mode}. Choose from: {list(MODE_CONFIG.keys())}")
            return
        download_graph(args.mode, bbox, args.force)
    else:
        download_all(bbox, args.force)


if __name__ == "__main__":
    main()
