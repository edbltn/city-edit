#!/usr/bin/env python3
"""
Rename a map's slug, leaving a redirect behind (CLI).

The old slug becomes a `map_redirects` row: /m/<old> keeps resolving (the
client follows it, preserving deep-link params and merging --append-query —
how a printed QR campaign gets retro-tagged with ?src=…), and the old slug
stays reserved (slug_available refuses it forever). Postgres moves in ONE
transaction; Redis (the only store the heatmap serves from) is then rebuilt
under the new slug and the old slug's keys are purged.

Usage:
    python rename_map.py nyc-intersections nyc-crossings \
        --append-query "src=qr-poster" \
        --note "2026-07 QR poster campaign printed without a src tag"

Against prod: run through the bastion tunnels (Postgres :5433, Redis :6380),
passing the prod URLs inline — never repoint server/.env. Full runbook:
docs/url-routing.md "Renaming a map slug".

In-process map caches on running Flask instances (30-60s TTL) age out on
their own; until then the old slug may briefly serve its old config.
"""
import argparse
import json
import logging
import os
import sys

import redis
from dotenv import load_dotenv

load_dotenv()  # DATABASE_URL / REDIS_HOST, read at import by database.py

import block_votes
import database
import vote_migration
import vote_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rename_map")


def _redis_client():
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    try:
        c = redis.Redis(host=host, port=port, db=0, decode_responses=True)
        c.ping()
        return c
    except redis.ConnectionError as e:
        logger.warning(f"Redis unavailable ({e}); DB renamed only — run again or "
                       f"rebuild_redis_for_map manually before the map will serve votes")
        return None


def main():
    ap = argparse.ArgumentParser(description="Rename a map slug, leaving a redirect")
    ap.add_argument("old_slug")
    ap.add_argument("new_slug")
    ap.add_argument("--append-query", default=None,
                    help='Query string the redirect merges into the target URL, '
                         'e.g. "src=qr-poster" (existing params win)')
    ap.add_argument("--note", default=None,
                    help="Why this redirect exists (stored on the row)")
    args = ap.parse_args()

    if not database.DATABASE_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    m = database.get_map(args.old_slug, with_vote_count=False)
    if not m:
        logger.error(f"No map with slug '{args.old_slug}'")
        sys.exit(1)
    mode_int = vote_store.mode_to_int(m.get("mode", "walk"))

    result = database.rename_map_slug(
        args.old_slug, args.new_slug,
        append_query=args.append_query, note=args.note,
    )
    logger.info(f"[RENAME] {result}")

    rc = _redis_client()
    fields = None
    if rc is not None:
        vote_store.load_vote_types()
        fields = vote_migration.rebuild_redis_for_map(rc, args.new_slug, mode_int)
        # Old-slug leftovers: aggregate hash + revision, and the derived block
        # state (bd:/bagg:). The new slug's block state rebuilds lazily from
        # Postgres on its next /api/graph-votes build.
        rc.delete(vote_store.hash_key(args.old_slug))
        rc.delete(vote_store.revision_key(args.old_slug))
        block_votes.clear(rc, args.old_slug, mode_int)
        block_votes.clear(rc, args.new_slug, mode_int)
        logger.info(f"[RENAME] Redis rebuilt under '{args.new_slug}' "
                    f"({fields} fields), old keys purged")

    print(json.dumps({**result, "appendQuery": args.append_query,
                      "redisFields": fields}, indent=2))


if __name__ == "__main__":
    main()
