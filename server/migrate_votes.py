#!/usr/bin/env python3
"""
Re-snap stored votes onto the current graph (CLI / backfill).

After a graph rebuild, edge ids shift; this walks every map, re-snaps each vote's
lat/lon anchor to the current graph's nearest edge, rebuilds Redis, and (best
-effort) repairs orphaned vote-type ids. Idempotent — safe to re-run.

Usage:
    python migrate_votes.py                 # all maps: repair types + resnap
    python migrate_votes.py --map nyc-bikes # one map
    python migrate_votes.py --no-repair     # resnap only, leave vote types alone
    python migrate_votes.py --dry-run       # report what would change, write nothing
"""
import argparse
import json
import logging
import os
import sys

import redis
from dotenv import load_dotenv

load_dotenv()  # DATABASE_URL / REDIS_HOST, read at import by database.py

import database
import vote_migration
import vote_store
from cities import get_city, DEFAULT_CITY_ID
from graph_registry import GraphRegistry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_votes")


def _redis_client():
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    try:
        c = redis.Redis(host=host, port=port, db=0, decode_responses=True)
        c.ping()
        return c
    except redis.ConnectionError as e:
        logger.warning(f"Redis unavailable ({e}); will re-snap DB only, skip Redis rebuild")
        return None


def main():
    ap = argparse.ArgumentParser(description="Re-snap votes onto the current graph")
    ap.add_argument("--map", help="Only this map slug (default: all maps)")
    ap.add_argument("--no-repair", action="store_true", help="Skip vote-type repair")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report orphaned vote types + anchor counts; write nothing")
    args = ap.parse_args()

    if not database.DATABASE_URL:
        logger.error("DATABASE_URL not set — nothing to migrate")
        sys.exit(1)

    vote_store.load_vote_types()
    rc = None if args.dry_run else _redis_client()
    registry = GraphRegistry(redis_client=rc, max_loaded=1)

    maps = database.list_maps()
    if args.map:
        maps = [m for m in maps if m["slug"] == args.map]
        if not maps:
            logger.error(f"No such map '{args.map}'")
            sys.exit(1)

    reports = []
    for m in maps:
        slug = m["slug"]
        city = get_city(m.get("cityId")) or get_city(DEFAULT_CITY_ID)
        network = m.get("network") or "streets"
        mode_int = vote_store.mode_to_int(m.get("mode", "walk"))
        labels = [vt["label"] for vt in (m.get("voteTypes") or []) if vt.get("label")]

        if args.dry_run:
            orphans = database.orphaned_vote_type_counts(slug)
            anchors = database.get_edge_vote_anchors(slug)
            logger.info(f"[DRY:{slug}] {len(anchors)} anchored votes, "
                        f"orphan types={orphans}")
            reports.append({"map": slug, "anchored": len(anchors), "orphans": orphans})
            continue

        cg = registry.get(city, network)
        report = vote_migration.migrate_map(
            cg, rc, slug, mode_int, labels, repair=not args.no_repair
        )
        reports.append(report)
        logger.info(f"[{slug}] done: {json.dumps(report.get('resnap', {}))}")

    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
