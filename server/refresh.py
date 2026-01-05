#!/usr/bin/env python3
"""
Daily OSM refresh script for Roam router.

Downloads fresh OSM data, rebuilds graphs, detects changes,
and invalidates affected cache entries.

Usage:
    python refresh.py              # Full refresh
    python refresh.py --check      # Check for changes without rebuilding
    python refresh.py --force      # Force rebuild even if no changes

Can be run as a cron job:
    0 4 * * * cd /path/to/server && python refresh.py >> /var/log/roam-refresh.log 2>&1
"""

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

import redis

from tiles import (
    get_all_nyc_tiles, compute_tile_hash, compute_all_tile_hashes
)
from roam_router import RoamRouter, CACHE_DIR, MODE_CONFIG
from roam_cache import (
    get_all_tile_hashes, set_all_tile_hashes,
    invalidate_tiles, clear_cache, get_cache_stats
)

# Metadata file for tracking refresh state
METADATA_FILE = CACHE_DIR / "metadata.json"
TILE_HASHES_FILE = CACHE_DIR / "tile_hashes.json"


def get_redis_client():
    """Create Redis client from environment."""
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    try:
        client = redis.Redis(
            host=redis_host,
            port=6379,
            db=0,
            decode_responses=True
        )
        client.ping()
        return client
    except redis.ConnectionError:
        print(f"Warning: Could not connect to Redis at {redis_host}")
        return None


def load_metadata() -> dict:
    """Load refresh metadata from disk."""
    if METADATA_FILE.exists():
        with open(METADATA_FILE, "r") as f:
            return json.load(f)
    return {}


def save_metadata(metadata: dict):
    """Save refresh metadata to disk."""
    CACHE_DIR.mkdir(exist_ok=True)
    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=2)


def load_tile_hashes_from_disk() -> dict[str, str]:
    """Load tile hashes from disk (fallback if Redis unavailable)."""
    if TILE_HASHES_FILE.exists():
        with open(TILE_HASHES_FILE, "r") as f:
            return json.load(f)
    return {}


def save_tile_hashes_to_disk(hashes: dict[str, str]):
    """Save tile hashes to disk."""
    CACHE_DIR.mkdir(exist_ok=True)
    with open(TILE_HASHES_FILE, "w") as f:
        json.dump(hashes, f)


def detect_changes(old_hashes: dict[str, str],
                   new_hashes: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    """
    Detect which tiles have changed between two hash sets.

    Returns:
        Tuple of (added_tiles, removed_tiles, modified_tiles)
    """
    old_keys = set(old_hashes.keys())
    new_keys = set(new_hashes.keys())

    added = list(new_keys - old_keys)
    removed = list(old_keys - new_keys)

    modified = []
    for tile_id in old_keys & new_keys:
        if old_hashes[tile_id] != new_hashes[tile_id]:
            modified.append(tile_id)

    return added, removed, modified


def refresh(force: bool = False, check_only: bool = False) -> dict:
    """
    Perform refresh operation.

    Args:
        force: Force rebuild even if no changes detected
        check_only: Just check for changes, don't rebuild

    Returns:
        Summary dict with refresh results
    """
    start_time = datetime.utcnow()
    print(f"Roam refresh started at {start_time.isoformat()}")

    # Connect to Redis
    redis_client = get_redis_client()

    # Load old hashes (try Redis first, fall back to disk)
    if redis_client:
        old_hashes = get_all_tile_hashes(redis_client)
        if not old_hashes:
            old_hashes = load_tile_hashes_from_disk()
    else:
        old_hashes = load_tile_hashes_from_disk()

    print(f"Loaded {len(old_hashes)} existing tile hashes")

    # Build router (this downloads/loads OSM data)
    router = RoamRouter(redis_client)

    if check_only:
        # Just check if cache exists
        unified_cache = CACHE_DIR / "roam_unified.gpickle"
        if unified_cache.exists():
            print("Super-graph cache exists")
            metadata = load_metadata()
            print(f"Last refresh: {metadata.get('last_refresh', 'unknown')}")
        else:
            print("No super-graph cache found")
        return {"status": "check_only", "cache_exists": unified_cache.exists()}

    # Load or build the super-graph
    router.load_or_build(force_rebuild=force)

    # Compute new tile hashes
    print("Computing tile hashes...")
    new_hashes = {}
    all_tiles = get_all_nyc_tiles()
    print(f"Processing {len(all_tiles)} tiles...")

    # We need to compute hashes from the unified graph
    # Since nodes are (mode, node_id), we'll hash based on the underlying mode graphs
    for mode, G in router.graphs.items():
        mode_hashes = compute_all_tile_hashes(G)
        for tile_id, hash_val in mode_hashes.items():
            # Combine hashes from all modes
            if tile_id not in new_hashes:
                new_hashes[tile_id] = hash_val
            else:
                # XOR combine hashes (simple combination)
                old = int(new_hashes[tile_id], 16)
                new = int(hash_val, 16)
                combined = old ^ new
                new_hashes[tile_id] = format(combined, '016x')

    print(f"Computed {len(new_hashes)} tile hashes")

    # Detect changes
    added, removed, modified = detect_changes(old_hashes, new_hashes)
    all_changed = added + removed + modified

    print(f"Changes detected: {len(added)} added, {len(removed)} removed, {len(modified)} modified")

    # Invalidate cache for changed tiles
    if all_changed and redis_client:
        print(f"Invalidating {len(all_changed)} tiles...")
        invalidated = invalidate_tiles(redis_client, all_changed)
        print(f"Invalidated {invalidated} cached paths")
    else:
        invalidated = 0

    # Save new hashes
    if redis_client:
        set_all_tile_hashes(redis_client, new_hashes)
    save_tile_hashes_to_disk(new_hashes)

    # Update metadata
    end_time = datetime.utcnow()
    metadata = {
        "last_refresh": end_time.isoformat(),
        "duration_seconds": (end_time - start_time).total_seconds(),
        "tiles_added": len(added),
        "tiles_removed": len(removed),
        "tiles_modified": len(modified),
        "paths_invalidated": invalidated,
        "total_tiles": len(new_hashes),
        "node_counts": {
            mode: router.graphs[mode].number_of_nodes()
            for mode in router.graphs
        },
        "edge_counts": {
            mode: router.graphs[mode].number_of_edges()
            for mode in router.graphs
        },
        "super_graph_nodes": router.super_graph.number_of_nodes() if router.super_graph else 0,
        "super_graph_edges": router.super_graph.number_of_edges() if router.super_graph else 0,
        "portal_count": len(router.portals)
    }
    save_metadata(metadata)

    # Print summary
    print("\n" + "=" * 50)
    print("REFRESH COMPLETE")
    print("=" * 50)
    print(f"Duration: {metadata['duration_seconds']:.1f} seconds")
    print(f"Tiles changed: {len(all_changed)}")
    print(f"Paths invalidated: {invalidated}")
    print(f"Super-graph: {metadata['super_graph_nodes']} nodes, {metadata['super_graph_edges']} edges")
    print(f"Portals: {metadata['portal_count']}")

    if redis_client:
        stats = get_cache_stats(redis_client)
        print(f"Cache stats: {stats}")

    return metadata


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Refresh Roam routing data from OSM"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force rebuild even if no changes detected"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check for changes without rebuilding"
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear all cached routes"
    )

    args = parser.parse_args()

    if args.clear_cache:
        redis_client = get_redis_client()
        if redis_client:
            deleted = clear_cache(redis_client)
            print(f"Cleared {deleted} cache entries")
        else:
            print("No Redis connection available")
        return

    result = refresh(force=args.force, check_only=args.check)

    # Exit with error if refresh failed
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
