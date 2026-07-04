#!/usr/bin/env python3
"""
Per-city OSM walk-graph builder + weekly refresh.

Builds the networkx walk graph (via pyosmium, from the same PBF + foot filter as
OSRM) for a given city into its own data directory: osm_data/<city>/walk_graph.pkl
+ metadata.json. OSRM routing datasets are built separately by the per-city OSRM
containers (see docker-compose.yml).

Usage:
    python refresh_osm.py                      # build the default city (nyc) if missing
    python refresh_osm.py --city sf            # build San Francisco
    python refresh_osm.py --city sf --force    # force rebuild
    python refresh_osm.py --all                # build every registered city
    python refresh_osm.py --check              # report status without changes

Can be run as a cron job:
    0 3 * * 0 cd /path/to/server && python refresh_osm.py --all >> /var/log/osm-refresh.log 2>&1
"""
import os
import sys
import json
import logging
import argparse
import subprocess
from datetime import datetime
from pathlib import Path

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent))

import redis

from cities import CITIES, DEFAULT_CITY_ID, get_city

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Base directory; each city builds into osm_data/<city>/
OSM_DATA_DIR = Path(__file__).parent / "osm_data"

# Legacy/test build targets (smaller NYC subsets for fast local builds). These are
# build-only bboxes — not selectable cities — and write to osm_data/<name>/.
TEST_BBOXES: dict[str, tuple] = {
    "fidi": (40.7025, -74.015, 40.715, -74.000),
    "downtown": (40.700, -74.020, 40.754, -73.970),
    "manhattan": (40.700, -74.020, 40.880, -73.907),
}


def bbox_for(target: str) -> tuple:
    city = get_city(target)
    if city is not None:
        return city.bbox
    if target in TEST_BBOXES:
        return TEST_BBOXES[target]
    raise ValueError(f"Unknown city/region '{target}'. "
                     f"Known: {list(CITIES) + list(TEST_BBOXES)}")


def data_dir_for(target: str) -> Path:
    return OSM_DATA_DIR / target


def metadata_file_for(target: str) -> Path:
    return data_dir_for(target) / "metadata.json"


def get_redis_client():
    """Create Redis client from environment."""
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))
    try:
        client = redis.Redis(host=redis_host, port=redis_port, db=0, decode_responses=True)
        client.ping()
        logger.info(f"Connected to Redis at {redis_host}:{redis_port}")
        return client
    except redis.ConnectionError as e:
        logger.warning(f"Could not connect to Redis: {e}")
        return None


def load_metadata(target: str) -> dict:
    path = metadata_file_for(target)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_metadata(target: str, data: dict):
    data_dir_for(target).mkdir(parents=True, exist_ok=True)
    metadata_file_for(target).write_text(json.dumps(data, indent=2))


def invalidate_route_cache(redis_client) -> int:
    """Clear cached routes after an OSM update."""
    if redis_client is None:
        return 0

    logger.info("Invalidating route cache...")
    patterns = ["route:walk:*", "route:coord:*", "roam:path:*", "roam:tile_paths:*", "roam:tile:*"]
    total_deleted = 0
    for pattern in patterns:
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=1000)
            if keys:
                total_deleted += redis_client.delete(*keys)
            if cursor == 0:
                break
    logger.info(f"Deleted {total_deleted} cached route entries")
    return total_deleted


def signal_reload(redis_client, target: str, version: str):
    """Signal running instances to reload a city's graph via Redis pub/sub."""
    if redis_client is None:
        return
    redis_client.publish("graph_reload", json.dumps({"city": target, "version": version}))
    logger.info(f"Published reload signal for '{target}' (version {version})")


def build_city(target: str, force: bool = False, check_only: bool = False) -> dict:
    """Build (or rebuild) the walk graph for a single city/region."""
    from python_router import build_graph

    bbox = bbox_for(target)
    out_dir = data_dir_for(target)
    graph_path = out_dir / "walk_graph.pkl"
    logger.info(f"[{target}] bbox={bbox}, data_dir={out_dir}")

    prev = load_metadata(target)
    if check_only:
        return {
            "city": target,
            "status": "check_only",
            "exists": graph_path.exists(),
            "version": prev.get("version"),
            "built_at": prev.get("built_at"),
        }

    if graph_path.exists() and not force:
        logger.info(f"[{target}] graph already present, skipping (use --force to rebuild)")
        return {"city": target, "status": "unchanged", "version": prev.get("version")}

    # Build from the same OSM extract OSRM uses (foot-aligned), so the votable graph
    # is a superset of OSRM's network. Cities have their own pbf_url; the legacy
    # TEST_BBOXES are NYC subsets, so they use NYC's extract.
    city = get_city(target)
    pbf_url = city.pbf_url if city is not None else get_city(DEFAULT_CITY_ID).pbf_url

    start = datetime.utcnow()
    stats = build_graph(bbox, str(out_dir), pbf_url=pbf_url)
    duration = (datetime.utcnow() - start).total_seconds()

    version = f"{target}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    metadata = {
        "city": target,
        "version": version,
        "built_at": datetime.utcnow().isoformat(),
        "build_duration_seconds": duration,
        "bbox": list(bbox),
        "nodes": stats.get("nodes"),
        "edges": stats.get("edges"),
        "source": "pbf",
        "pbf_url": pbf_url,
    }
    save_metadata(target, metadata)

    redis_client = get_redis_client()
    deleted = invalidate_route_cache(redis_client)
    signal_reload(redis_client, target, version)
    metadata["routes_invalidated"] = deleted

    logger.info(f"[{target}] BUILD COMPLETE — {stats.get('nodes')} nodes, "
                f"{stats.get('edges')} edges in {duration:.1f}s")
    return metadata


def main():
    parser = argparse.ArgumentParser(description="Build per-city OSM walk graphs")
    parser.add_argument("--city", default=DEFAULT_CITY_ID,
                        help=f"City/region to build (default: {DEFAULT_CITY_ID}). "
                             f"Cities: {list(CITIES)}; test regions: {list(TEST_BBOXES)}")
    parser.add_argument("--region", help="Alias for --city (back-compat)")
    parser.add_argument("--all", action="store_true", help="Build every registered city")
    parser.add_argument("--force", action="store_true", help="Force rebuild even if present")
    parser.add_argument("--check", action="store_true", help="Report status without changes")
    parser.add_argument("--blocks", action="store_true",
                        help="After the graph, build the Layer-2 blocks + edge→block "
                             "bake (streetscape_blocks/build_city_blocks.sh; needs the "
                             "geo venv). A rebuilt graph gets a new topology_etag, which "
                             "invalidates any prior bake — pass this on every rebuild of "
                             "a city that shows blocks.")
    args = parser.parse_args()

    targets = list(CITIES) if args.all else [args.region or args.city]

    try:
        results = [build_city(t, force=args.force, check_only=args.check) for t in targets]
        if args.blocks and not args.check:
            script = Path(__file__).parent / "streetscape_blocks" / "build_city_blocks.sh"
            for t in targets:
                logger.info(f"[{t}] building Layer-2 blocks (docs/three-layer-model.md §2.2)")
                subprocess.run([str(script), t], check=True)
        print(json.dumps(results if args.all else results[0], indent=2))
    except Exception as e:
        logger.error(f"Build failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
