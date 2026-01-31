#!/usr/bin/env python3
"""
Weekly OSM data refresh with CH graph rebuilding.

Downloads fresh OSM data from Geofabrik, rebuilds CH graphs if data changed,
and invalidates the route cache.

Usage:
    python refresh_osm.py                    # Check and rebuild if needed (fidi region)
    python refresh_osm.py --force            # Force rebuild
    python refresh_osm.py --region manhattan # Build full Manhattan (higher memory)
    python refresh_osm.py --region nyc-metro # Build full NYC metro (high memory)
    python refresh_osm.py --download-only    # Just download, don't build
    python refresh_osm.py --check            # Check status without changes

Can be run as a cron job:
    0 3 * * 0 cd /path/to/server && python refresh_osm.py >> /var/log/osm-refresh.log 2>&1
"""
import os
import sys
import json
import hashlib
import logging
import argparse
import requests
from datetime import datetime
from pathlib import Path

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent))

import redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Configuration
OSM_DATA_DIR = Path(__file__).parent / "osm_data"
METADATA_FILE = OSM_DATA_DIR / "metadata.json"

# NYC metro area PBF from Geofabrik
# This includes NYC, parts of NJ, Westchester, and Long Island
GEOFABRIK_URL = "https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf"

# NYC metro bounding box
NYC_METRO_BBOX = (
    40.49,   # min_lat (south)
    -74.26,  # min_lon (west)
    41.05,   # max_lat (north)
    -73.65   # max_lon (east)
)

# Full NYC map bounds (matches client config exactly)
NYC_BBOX = (
    40.4774,  # min_lat (south)
    -74.2591, # min_lon (west)
    40.9176,  # max_lat (north)
    -73.7004  # max_lon (east)
)

# Manhattan-only bounding box (smaller, memory-safe)
MANHATTAN_BBOX = (
    40.700,  # min_lat (south - Battery Park)
    -74.020, # min_lon (west - Hudson River)
    40.880,  # max_lat (north - Inwood)
    -73.907  # max_lon (east - East River)
)

# FiDi bounding box - very small test area for fast builds
FIDI_BBOX = (
    40.7025,  # min_lat (south - Bowling Green)
    -74.015,  # min_lon (west - Hudson)
    40.715,   # max_lat (north - City Hall)
    -74.000   # max_lon (east - FDR)
)

# Downtown Manhattan bounding box (Battery Park to 34th St)
DOWNTOWN_BBOX = (
    40.700,   # min_lat (south - Battery Park)
    -74.020,  # min_lon (west - Hudson River)
    40.754,   # max_lat (north - 34th Street)
    -73.970   # max_lon (east - East River)
)

# Available regions for --region flag
REGIONS = {
    "downtown": DOWNTOWN_BBOX,
    "fidi": FIDI_BBOX,
    "manhattan": MANHATTAN_BBOX,
    "nyc": NYC_BBOX,
    "nyc-metro": NYC_METRO_BBOX,
}


def get_redis_client():
    """Create Redis client from environment."""
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))

    try:
        client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=0,
            decode_responses=True
        )
        client.ping()
        logger.info(f"Connected to Redis at {redis_host}:{redis_port}")
        return client
    except redis.ConnectionError as e:
        logger.warning(f"Could not connect to Redis: {e}")
        return None


def load_metadata() -> dict:
    """Load previous build metadata."""
    if METADATA_FILE.exists():
        try:
            return json.loads(METADATA_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_metadata(data: dict):
    """Save build metadata."""
    OSM_DATA_DIR.mkdir(exist_ok=True)
    METADATA_FILE.write_text(json.dumps(data, indent=2))


def download_osm(region: str, bbox: tuple) -> tuple[Path, str]:
    """
    Download OSM PBF file and compute hash.

    Note: Downloads full NY state file then filters by bbox during build.
    First download is slow (~485MB) but subsequent builds use cached file.

    Returns:
        Tuple of (file_path, sha256_hash)
    """
    OSM_DATA_DIR.mkdir(exist_ok=True)
    pbf_path = OSM_DATA_DIR / "new-york-metro.osm.pbf"

    # Skip download if file exists and is recent (within 7 days)
    if pbf_path.exists():
        age_days = (datetime.utcnow() - datetime.fromtimestamp(pbf_path.stat().st_mtime)).days
        if age_days < 7:
            logger.info(f"Using cached PBF file ({age_days} days old)")
            with open(pbf_path, "rb") as f:
                hasher = hashlib.sha256()
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            return pbf_path, hasher.hexdigest()[:16]

    logger.info(f"Downloading from {GEOFABRIK_URL}...")

    response = requests.get(GEOFABRIK_URL, stream=True, timeout=600)
    response.raise_for_status()

    total_size = int(response.headers.get("content-length", 0))
    downloaded = 0
    hasher = hashlib.sha256()

    with open(pbf_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            hasher.update(chunk)
            downloaded += len(chunk)

            # Progress update every 10MB
            if downloaded % (10 * 1024 * 1024) == 0:
                pct = (downloaded / total_size * 100) if total_size else 0
                logger.info(f"  Downloaded {downloaded / 1e6:.1f}MB ({pct:.1f}%)")

    file_hash = hasher.hexdigest()[:16]
    logger.info(f"Download complete: {pbf_path.stat().st_size / 1e6:.1f}MB, hash: {file_hash}")

    return pbf_path, file_hash


def invalidate_route_cache(redis_client) -> int:
    """
    Clear all cached routes after OSM update.

    Returns:
        Number of keys deleted
    """
    if redis_client is None:
        return 0

    logger.info("Invalidating route cache...")

    patterns = ["roam:path:*", "roam:tile_paths:*", "roam:tile:*"]
    total_deleted = 0

    for pattern in patterns:
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=1000)
            if keys:
                deleted = redis_client.delete(*keys)
                total_deleted += deleted
            if cursor == 0:
                break

    logger.info(f"Deleted {total_deleted} cached route entries")
    return total_deleted


def signal_reload(redis_client, version: str):
    """Signal running instances to reload graphs via Redis pub/sub."""
    if redis_client is None:
        return

    channel = "graph_reload"
    redis_client.publish(channel, version)
    logger.info(f"Published reload signal to channel '{channel}'")


def build_graphs(pbf_path: Path, bbox: tuple = None) -> dict:
    """
    Build walk graph from OSM using Python router.

    Note: The PBF file is no longer used. osmnx downloads directly.

    Returns:
        Build statistics
    """
    from python_router import build_graph

    logger.info("Building walk graph...")
    start_time = datetime.utcnow()

    stats = build_graph(bbox, str(OSM_DATA_DIR))

    duration = (datetime.utcnow() - start_time).total_seconds()
    logger.info(f"Graph building complete in {duration:.1f}s")

    return {"build_duration_seconds": duration, **stats}


def refresh(force: bool = False, download_only: bool = False, check_only: bool = False, region: str = "fidi") -> dict:
    """
    Main refresh logic.

    Args:
        force: Force rebuild even if data unchanged
        download_only: Only download, don't build
        check_only: Only check status, no changes
        region: Geographic region to build ("manhattan" or "nyc-metro")

    Returns:
        Summary dict with refresh results
    """
    bbox = REGIONS[region]
    logger.info(f"Region: {region}, bbox: {bbox}")
    start_time = datetime.utcnow()
    logger.info(f"OSM refresh started at {start_time.isoformat()}")

    # Load previous metadata
    metadata = load_metadata()
    old_hash = metadata.get("osm_hash")
    logger.info(f"Previous OSM hash: {old_hash or 'none'}")

    if check_only:
        return {
            "status": "check_only",
            "previous_hash": old_hash,
            "last_refresh": metadata.get("built_at"),
            "version": metadata.get("version")
        }

    # Connect to Redis
    redis_client = get_redis_client()

    # Download new OSM data
    pbf_path, new_hash = download_osm(region, bbox)

    # Check if rebuild needed
    if old_hash == new_hash and not force:
        logger.info(f"OSM data unchanged (hash: {new_hash}), skipping rebuild")
        return {
            "status": "unchanged",
            "hash": new_hash,
            "duration_seconds": (datetime.utcnow() - start_time).total_seconds()
        }

    logger.info(f"OSM data changed: {old_hash} -> {new_hash}")

    if download_only:
        logger.info("Download-only mode, skipping build")
        return {
            "status": "downloaded",
            "hash": new_hash,
            "path": str(pbf_path)
        }

    # Build new CH graphs
    build_stats = build_graphs(pbf_path, bbox)

    # Invalidate route cache
    deleted = invalidate_route_cache(redis_client)

    # Generate version string
    version = f"{new_hash}_{datetime.utcnow().strftime('%Y%m%d')}"

    # Signal running instances to reload
    signal_reload(redis_client, version)

    # Update metadata
    end_time = datetime.utcnow()
    new_metadata = {
        "osm_hash": new_hash,
        "version": version,
        "built_at": end_time.isoformat(),
        "refresh_duration_seconds": (end_time - start_time).total_seconds(),
        "build_duration_seconds": build_stats["build_duration_seconds"],
        "routes_invalidated": deleted,
        "source": GEOFABRIK_URL,
        "region": region,
        "bbox": bbox
    }
    save_metadata(new_metadata)

    logger.info("=" * 50)
    logger.info("REFRESH COMPLETE")
    logger.info("=" * 50)
    logger.info(f"Version: {version}")
    logger.info(f"Duration: {new_metadata['refresh_duration_seconds']:.1f}s")
    logger.info(f"Routes invalidated: {deleted}")

    return new_metadata


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Refresh OSM routing data and rebuild CH graphs"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force rebuild even if data unchanged"
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Only download OSM data, don't build graphs"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check status without making changes"
    )
    parser.add_argument(
        "--region",
        choices=list(REGIONS.keys()),
        default="downtown",
        help="Geographic region to build (default: downtown)"
    )

    args = parser.parse_args()

    try:
        result = refresh(
            force=args.force,
            download_only=args.download_only,
            check_only=args.check,
            region=args.region
        )
        print(json.dumps(result, indent=2))

        if "error" in result:
            sys.exit(1)

    except Exception as e:
        logger.error(f"Refresh failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
