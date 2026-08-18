# Referenced by: docs/algorithms/06-ranking.md (list_maps — how the landing
#   page is ordered) and docs/algorithms/07-counts.md
#   (count_unique_voters_for_edges — the honest distinct-voter number).
"""
PostgreSQL database module for persistent vote storage.

Provides persistence layer for votes, complementing Redis real-time state.
Redis handles fast reads/writes and pub/sub; Postgres stores permanent history.
"""
import logging
import os
import re
import time
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2.extras import execute_values
from psycopg2.pool import ThreadedConnectionPool

import vote_identity

# Under the gevent worker, plain psycopg2 blocks the event-loop hub for the
# full duration of every query — one slow aggregate froze ALL in-flight
# requests on the instance. psycogreen's wait callback makes psycopg2 yield to
# the hub while waiting on the socket. Only safe/meaningful when gevent has
# monkey-patched the runtime (prod gunicorn); plain `python app.py` skips it.
try:
    from gevent import monkey as _gevent_monkey

    if _gevent_monkey.is_module_patched("socket"):
        from psycogreen.gevent import patch_psycopg

        patch_psycopg()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Database connection string from environment
DATABASE_URL = os.environ.get("DATABASE_URL")

# Connection pool. A pool (not the old shared singleton connection) is REQUIRED
# once psycopg2 yields to the gevent hub mid-query: two greenlets interleaving
# on one connection raise "another command is already in progress". Default
# sized so 4 app instances stay under db-f1-micro's ~25-connection ceiling;
# DB_POOL_MAX overrides where the DB allows more (votes now run concurrently
# per voter, so the pool is the write path's throughput governor).
_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "5"))
_pool: Optional[ThreadedConnectionPool] = None


def _get_pool() -> ThreadedConnectionPool:
    global _pool

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable not set")

    if _pool is None:
        _pool = ThreadedConnectionPool(1, _POOL_MAX, DATABASE_URL)
    return _pool


@contextmanager
def get_cursor():
    """Context manager for a pooled database cursor (autocommit)."""
    pool = _get_pool()

    # getconn raises PoolError immediately when all connections are checked
    # out; briefly wait-and-retry instead so a burst queues rather than 500s
    # (time.sleep yields to the hub under gevent).
    conn = None
    for attempt in range(50):
        try:
            conn = pool.getconn()
            break
        except psycopg2.pool.PoolError:
            time.sleep(0.1)
    if conn is None:
        raise RuntimeError("Database connection pool exhausted")

    try:
        if conn.closed:
            # Server dropped it while pooled (idle timeout, failover) — swap in
            # a fresh one under the same pool slot.
            pool.putconn(conn, close=True)
            conn = pool.getconn()
        conn.autocommit = True
        cursor = conn.cursor()
        try:
            yield cursor
        finally:
            cursor.close()
    except psycopg2.OperationalError:
        # Connection went bad mid-use: drop it from the pool, not back in.
        pool.putconn(conn, close=True)
        conn = None
        raise
    finally:
        if conn is not None:
            pool.putconn(conn)


def _migrate_edge_votes(cursor):
    """Migrate a legacy edge_votes table to the clean schema, in place (idempotent).

    Legacy shape: (packed_key, edge_id, mode, vote_type_id, ip_hash, direction).
    Target shape: (map_slug, edge_id, vote_type_id, device_id, ip_hash, direction),
    deduped per (map_slug, edge_id, vote_type_id, device_id). No data loss.
    """
    cursor.execute("ALTER TABLE edge_votes ADD COLUMN IF NOT EXISTS map_slug TEXT")
    cursor.execute("ALTER TABLE edge_votes ADD COLUMN IF NOT EXISTS device_id VARCHAR(16)")
    cursor.execute(
        "ALTER TABLE edge_votes ADD COLUMN IF NOT EXISTS direction SMALLINT NOT NULL DEFAULT 1"
    )
    # Legacy 'mode' smallint → preset map_slug, while that column still exists.
    cursor.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='edge_votes' AND column_name='mode') THEN
                UPDATE edge_votes SET map_slug = CASE mode
                    WHEN 0 THEN 'nyc-bikes' WHEN 1 THEN 'nyc-trees'
                    WHEN 2 THEN 'nyc-walkways' ELSE 'nyc-walkways' END
                WHERE map_slug IS NULL OR map_slug = '';
            END IF;
        END $$;
    """)
    # User identity: the old single ip_hash becomes the device id (the dedup key).
    cursor.execute("UPDATE edge_votes SET device_id = ip_hash WHERE device_id IS NULL")
    # Collapse any rows that now collide on the new identity (e.g. same map+edge+
    # type+device that previously differed only by the legacy mode bits).
    cursor.execute("""
        DELETE FROM edge_votes a USING edge_votes b
        WHERE a.id < b.id
          AND a.map_slug = b.map_slug AND a.edge_id = b.edge_id
          AND a.vote_type_id = b.vote_type_id AND a.device_id = b.device_id
    """)
    cursor.execute("ALTER TABLE edge_votes ALTER COLUMN map_slug SET NOT NULL")
    cursor.execute("ALTER TABLE edge_votes ALTER COLUMN device_id SET NOT NULL")
    cursor.execute("ALTER TABLE edge_votes ALTER COLUMN ip_hash DROP NOT NULL")
    cursor.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='edge_votes_packed_key_ip_hash_key') THEN
                ALTER TABLE edge_votes DROP CONSTRAINT edge_votes_packed_key_ip_hash_key;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='edge_votes_map_packed_ip_key') THEN
                ALTER TABLE edge_votes DROP CONSTRAINT edge_votes_map_packed_ip_key;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='edge_votes_identity_key') THEN
                ALTER TABLE edge_votes ADD CONSTRAINT edge_votes_identity_key
                UNIQUE (map_slug, edge_id, vote_type_id, device_id);
            END IF;
        END $$;
    """)
    # Drop the now-redundant denormalized/legacy columns.
    cursor.execute("ALTER TABLE edge_votes DROP COLUMN IF EXISTS packed_key")
    cursor.execute("ALTER TABLE edge_votes DROP COLUMN IF EXISTS mode")


def init_db():
    """Initialize database schema. Called on app startup."""
    if not DATABASE_URL:
        logger.warning("[DB] DATABASE_URL not set, skipping database initialization")
        return False

    try:
        with get_cursor() as cursor:
            # ── Packed-key vote tables ──
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vote_types (
                    id SERIAL PRIMARY KEY,
                    label TEXT UNIQUE NOT NULL
                )
            """)
            # Whether the type describes a ROUTE (corridor) or a POINT (spot)
            # proposal: 'route' | 'point' | NULL (unknown — pre-flag rows).
            # Preset labels are stamped by seed_presets(); user suggestions get
            # theirs from the cast that creates them; legacy NULLs are repaired
            # by backfill_vote_type_kinds().
            cursor.execute(
                "ALTER TABLE vote_types ADD COLUMN IF NOT EXISTS point_type TEXT"
            )

            # ── edge_votes: the canonical vote record ──
            # One row per (map, graph entity, vote type, voter). Identity:
            #   map_slug      → the "mode" (which map; edge_id space is per-map)
            #   edge_id       → graph entity
            #   vote_type_id  → vote type
            #   device_id     → user (stable device id; the dedup key)
            #   ip_hash       → user's IP (recorded for abuse/analytics, nullable)
            # direction is the up/down (+1/-1) of the vote.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS edge_votes (
                    id SERIAL PRIMARY KEY,
                    map_slug TEXT NOT NULL,
                    edge_id INT NOT NULL,
                    vote_type_id INT NOT NULL DEFAULT 0,
                    device_id VARCHAR(16) NOT NULL,
                    ip_hash VARCHAR(16),
                    direction SMALLINT NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            _migrate_edge_votes(cursor)
            # Migration anchor: the edge midpoint, so votes survive a graph
            # rebuild (edge_id indices shift) via resnap_votes_for_map().
            cursor.execute("ALTER TABLE edge_votes ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION")
            cursor.execute("ALTER TABLE edge_votes ADD COLUMN IF NOT EXISTS lon DOUBLE PRECISION")
            # updated_at = last time this device (re)voted the edge; the LRU key
            # for the per-IP cap's eviction (evict_lru_devices_for_edges). Backfill
            # legacy rows from created_at so seeded data keeps its original order.
            cursor.execute("ALTER TABLE edge_votes ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()")
            cursor.execute("UPDATE edge_votes SET updated_at = created_at WHERE updated_at IS NULL")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_edge_votes_edge ON edge_votes(edge_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_edge_votes_vt ON edge_votes(vote_type_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_edge_votes_map ON edge_votes(map_slug)")
            # Covers fetch_voted_vote_type_labels' GROUP BY vote_type / MAX(created_at)
            # as an index-only scan — that aggregate runs on the map-config path
            # (page load) and seq-scanned the map's rows per request without it.
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_edge_votes_map_vt_created "
                           "ON edge_votes(map_slug, vote_type_id, created_at)")
            # Serves get_voter_type_rows — the prior-state read on EVERY cast's
            # clear-then-cast plan. Without it that read is a full scan of the
            # map's rows (measured 9.4s per vote on prod nyc-bikes at 1.9M rows;
            # 0.2ms with it). The identity key can't help: it leads with edge_id.
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_edge_votes_map_vt_device "
                           "ON edge_votes(map_slug, vote_type_id, device_id)")
            # "Has this person ever voted?" — the backfill half of
            # database.note_map_open, asked once per visit on the page-load path
            # by the first-run flow. Both probes are single-column EXISTS
            # lookups; the composite index above can't serve them because it
            # leads with map_slug and the question is deliberately map-agnostic
            # — somebody who voted on the bikes map is not a first-timer on the
            # trees one.
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_edge_votes_device "
                           "ON edge_votes(device_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_edge_votes_ip "
                           "ON edge_votes(ip_hash)")

            # Vote-type lists: named collections (preset or user-created custom).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vote_type_lists (
                    id SERIAL PRIMARY KEY,
                    key TEXT UNIQUE,
                    name TEXT NOT NULL,
                    is_preset BOOLEAN NOT NULL DEFAULT FALSE,
                    featured BOOLEAN NOT NULL DEFAULT FALSE,
                    vote_types JSONB NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            # `featured` (added later) decouples "shows in the Propose-a-Map
            # picker" from `is_preset` ("system-seeded"), so a community-created
            # set can be promoted into the picker without being a system preset.
            cursor.execute(
                "ALTER TABLE vote_type_lists ADD COLUMN IF NOT EXISTS "
                "featured BOOLEAN NOT NULL DEFAULT FALSE"
            )

            # Maps (the user-facing "modes"): city × vote-type list + options.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS maps (
                    id SERIAL PRIMARY KEY,
                    slug TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    subtitle TEXT,
                    city_id TEXT NOT NULL,
                    vote_type_list_id INT REFERENCES vote_type_lists(id),
                    custom_vote_types JSONB,
                    allow_suggestions BOOLEAN NOT NULL DEFAULT TRUE,
                    passcode_hash TEXT,
                    subdomain TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    created_by_ip_hash VARCHAR(16)
                )
            """)
            cursor.execute("ALTER TABLE maps ADD COLUMN IF NOT EXISTS subtitle TEXT")
            cursor.execute("ALTER TABLE maps ADD COLUMN IF NOT EXISTS symbol TEXT")
            cursor.execute("ALTER TABLE maps ADD COLUMN IF NOT EXISTS style TEXT")
            # What the map votes on: "streets" (default) or a station network
            # (e.g. "ebikes"). NULL/absent reads back as "streets".
            cursor.execute("ALTER TABLE maps ADD COLUMN IF NOT EXISTS network TEXT")
            # Per-map override of the client's top-proposal support floor
            # (TOP_PROPOSAL_MIN_NET). NULL = client default. 0 admits any
            # net-positive proposal — used by curated/imported maps (e.g.
            # nyc-proposals, one vote per official DOT proposal) where the
            # crowdsourced >100-net bar would hide every entry.
            cursor.execute(
                "ALTER TABLE maps ADD COLUMN IF NOT EXISTS top_proposal_min_net INT"
            )
            # Where an imported vote came from. One row per synthetic voter of an
            # import batch (device_id = the hash /api/vote stored on its rows),
            # pointing at the document the proposal was scraped from — e.g. a
            # nycdotprojects.info project page behind a nyc-proposals vote. Keyed
            # by device_id, NOT edge ids, so a graph rebuild (which shifts edge
            # ids and resnaps votes) can't strand the attribution.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vote_sources (
                    map_slug TEXT NOT NULL,
                    device_id VARCHAR(16) NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT,
                    PRIMARY KEY (map_slug, device_id)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_maps_city ON maps(city_id)
            """)
            # A vanity subdomain points at exactly one map. Partial index so any
            # number of maps may have no subdomain (NULL) while set ones stay unique.
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_maps_subdomain
                ON maps(subdomain) WHERE subdomain IS NOT NULL
            """)

            # Slug redirects: after a map is renamed, its old slug lives here so
            # printed links (QR posters) keep working AND the old slug stays
            # reserved (slug_available checks both tables). append_query is a
            # query string ("src=qr-poster") merged into the target URL by the
            # client — the mechanism that retro-tags a campaign's traffic.
            # `note` records why the redirect exists; every row is surfaced in
            # docs/url-routing.md's redirect inventory.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS map_redirects (
                    from_slug TEXT PRIMARY KEY,
                    to_slug TEXT NOT NULL,
                    append_query TEXT,
                    note TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # ── Sticker codes: one row per printed sticker ──────────────────
            # A sticker is a QR code on a pole. Its code is minted at print time
            # (tools/stickers) and seeded here BEFORE the sheet goes in anyone's
            # bag — an unseeded code is a 404 at the kerb.
            #
            # The row starts life knowing only what was printed on it (which map,
            # which vote type, which line, which campaign tag). It does NOT know
            # where the sticker ended up, because nobody does until someone puts
            # it on a pole. The first scanner shares their location and votes,
            # `resolve_sticker` stamps lat/lon, and from then on the code IS that
            # place: every later scan skips the prompt and opens the vote there.
            #
            # That is why the resolved columns are write-once. A second visitor
            # standing across the street must not be able to drag a sticker's
            # identity down the block, and a re-run of the print seeder must not
            # reset one that is already live (see resolve_sticker / the seed SQL).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sticker_codes (
                    code TEXT PRIMARY KEY,
                    map_slug TEXT NOT NULL,
                    vote_type TEXT NOT NULL,
                    headline TEXT NOT NULL,
                    src_tag TEXT NOT NULL,
                    campaign TEXT,
                    lat DOUBLE PRECISION,
                    lon DOUBLE PRECISION,
                    resolved_at TIMESTAMP,
                    resolved_by VARCHAR(16),
                    scans INT NOT NULL DEFAULT 0,
                    first_scan_at TIMESTAMP,
                    last_scan_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            # What the code resolved TO. The original columns could only record
            # a point, which is half a proposal: a route-based vote is an
            # ordered list of waypoints, and a voter can change map or vote type
            # between scanning and casting. These carry the whole thing.
            #
            # Added by ALTER rather than folded into the CREATE above, because
            # the table already exists in prod with rows on it.
            #
            #   resolved_map_slug   the map the vote actually landed on, which
            #                       need NOT be the map that was printed
            #   resolved_vote_type  the vote type actually cast
            #   resolved_w          the canonical `w=` selection (see
            #                       client selection/serialize.ts) — one
            #                       waypoint for a point vote, several for a
            #                       route, forced-corridor tokens included
            for column, ddl in (
                ("resolved_map_slug", "TEXT"),
                ("resolved_vote_type", "TEXT"),
                ("resolved_w", "TEXT"),
            ):
                cursor.execute(
                    f"ALTER TABLE sticker_codes ADD COLUMN IF NOT EXISTS {column} {ddl}"
                )

            # "Which of this campaign's stickers have found a home?" is the
            # question the whole table exists to answer, so it gets the index.
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sticker_codes_campaign "
                "ON sticker_codes(campaign, resolved_at)"
            )

            # ── Who has opened a map before ─────────────────────────────
            # The first-run wall's whole trigger, and the reason it is a TABLE
            # rather than a reading of something we already have: the app's
            # existing record of map opens is the [MAPLOAD] log line, which is a
            # monitoring metric with no queryable store behind it.
            #
            # One row per counting key (vote_identity), not per open and not per
            # map: the question is "has this person ever opened a map of ANY
            # kind", so a second map must find the row the first one wrote.
            # `opens` and `last_seen` are kept because they cost one UPDATE that
            # was happening anyway and answer "how many of our visitors come
            # back" without a second table.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS map_visitors (
                    key_kind TEXT NOT NULL,
                    key_value TEXT NOT NULL,
                    first_seen TIMESTAMP NOT NULL DEFAULT NOW(),
                    last_seen TIMESTAMP NOT NULL DEFAULT NOW(),
                    opens INT NOT NULL DEFAULT 1,
                    PRIMARY KEY (key_kind, key_value)
                )
            """)

            logger.info("[DB] Database schema initialized successfully")
            return True

    except Exception as e:
        logger.error(f"[DB] Failed to initialize database: {e}")
        return False


def record_edge_votes(
    map_slug: str, edge_ids: list[int], vt_id: int,
    device_id: str, ip_hash: str | None = None, direction: int = 1,
    coords: dict[int, tuple[float, float]] | None = None,
):
    """Persist edge votes, scoped to a map and deduped per device.

    One row per (map, edge, vote_type, device); on conflict the direction is
    updated so a reversal (up↔down) overwrites the prior vote. `coords` maps
    edge_id → (lat, lon) of the edge midpoint — the migration anchor that lets
    votes survive a graph rebuild (see resnap_votes_for_map). lat/lon is only
    written when supplied, and kept (COALESCE) on conflict.
    """
    if not DATABASE_URL or not edge_ids:
        return

    dir_val = 1 if direction >= 0 else -1
    coords = coords or {}
    try:
        with get_cursor() as cursor:
            data = []
            for eid in edge_ids:
                lat, lon = coords.get(eid, (None, None))
                data.append((map_slug, eid, vt_id, device_id, ip_hash, dir_val, lat, lon))
            execute_values(
                cursor,
                """INSERT INTO edge_votes
                   (map_slug, edge_id, vote_type_id, device_id, ip_hash, direction, lat, lon)
                   VALUES %s
                   ON CONFLICT (map_slug, edge_id, vote_type_id, device_id)
                   DO UPDATE SET direction = EXCLUDED.direction,
                                 ip_hash = EXCLUDED.ip_hash,
                                 lat = COALESCE(EXCLUDED.lat, edge_votes.lat),
                                 lon = COALESCE(EXCLUDED.lon, edge_votes.lon),
                                 updated_at = NOW()""",
                data,
            )
            logger.info(f"[DB] Recorded {len(data)} edge votes for '{map_slug}' (dir={dir_val})")
    except Exception as e:
        logger.error(f"[DB] Failed to record edge votes: {e}")


def delete_edge_votes(
    map_slug: str, edge_ids: list[int], vt_id: int, device_id: str,
):
    """Remove a device's votes for a type on given edges (a toggle-off / un-vote)."""
    if not DATABASE_URL or not edge_ids:
        return
    try:
        with get_cursor() as cursor:
            cursor.execute(
                """DELETE FROM edge_votes
                   WHERE map_slug = %s AND vote_type_id = %s AND device_id = %s
                     AND edge_id = ANY(%s)""",
                (map_slug, vt_id, device_id, list(edge_ids)),
            )
            logger.info(f"[DB] Removed {cursor.rowcount} edge votes for '{map_slug}'")
    except Exception as e:
        logger.error(f"[DB] Failed to delete edge votes: {e}")


def get_edge_vote_anchors(map_slug: str) -> list[tuple[int, float, float]]:
    """(row_id, lat, lon) for every vote on a map that has a migration anchor."""
    if not DATABASE_URL:
        return []
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT id, lat, lon FROM edge_votes "
                "WHERE map_slug = %s AND lat IS NOT NULL AND lon IS NOT NULL",
                (map_slug,),
            )
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"[DB] Failed to read edge vote anchors: {e}")
        return []


def update_edge_vote_edge_id(row_id: int, new_edge_id: int) -> bool:
    """Re-point one vote row at a new edge_id (after a graph rebuild re-snap).

    Returns False if the move would collide with an existing identity row (the
    caller drops the loser), True otherwise.
    """
    if not DATABASE_URL:
        return False
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE edge_votes SET edge_id = %s WHERE id = %s", (new_edge_id, row_id)
            )
        return True
    except Exception:
        # Unique-identity collision: another row already holds this edge for the
        # same (map, type, device). Drop this now-duplicate row.
        try:
            with get_cursor() as cursor:
                cursor.execute("DELETE FROM edge_votes WHERE id = %s", (row_id,))
        except Exception as e:
            logger.error(f"[DB] Re-snap collision cleanup failed: {e}")
        return False


def get_voter_edge_directions(
    map_slug: str, edge_ids: list[int] | None, device_id: str
) -> dict[int, dict[int, int]]:
    """Return {edge_id: {vote_type_id: direction}} for one device on a map.

    edge_ids=None returns the device's FULL vote set for the map — the
    authoritative snapshot the client resets its local store from on load (a
    stale localStorage claiming votes the server lacks flips casts into
    unvotes; see docs/three-layer-model.md §4.1)."""
    if not DATABASE_URL or (edge_ids is not None and not edge_ids):
        return {}
    try:
        with get_cursor() as cursor:
            if edge_ids is None:
                cursor.execute(
                    """SELECT edge_id, vote_type_id, direction
                       FROM edge_votes
                       WHERE map_slug = %s AND device_id = %s""",
                    (map_slug, device_id),
                )
            else:
                cursor.execute(
                    """SELECT edge_id, vote_type_id, direction
                       FROM edge_votes
                       WHERE map_slug = %s AND device_id = %s AND edge_id = ANY(%s)""",
                    (map_slug, device_id, list(edge_ids)),
                )
            result: dict[int, dict[int, int]] = {}
            for edge_id, vt_id, direction in cursor.fetchall():
                result.setdefault(edge_id, {})[vt_id] = direction
            return result
    except Exception as e:
        logger.error(f"[DB] Failed to read voter edge directions: {e}")
        return {}


def count_devices_per_ip_for_edges(
    map_slug: str, edge_ids: list[int], vt_id: int, ip_hash: str,
    exclude_device_id: str | None = None,
) -> dict[int, int]:
    """{edge_id: # of DISTINCT devices this IP already votes it with} for a type.

    Powers the soft per-IP abuse cap (one IP minting many device_ids to re-vote
    the same edge). The caller's own device is excluded so a normal re-vote or
    reversal never counts against the cap. Rows with a NULL ip_hash (legacy, or
    imports keyed per-ride) are naturally ignored by the equality match.
    """
    if not DATABASE_URL or not edge_ids or not ip_hash:
        return {}
    try:
        with get_cursor() as cursor:
            cursor.execute(
                """SELECT edge_id, COUNT(DISTINCT device_id)
                   FROM edge_votes
                   WHERE map_slug = %s AND vote_type_id = %s AND ip_hash = %s
                     AND edge_id = ANY(%s) AND device_id <> %s
                   GROUP BY edge_id""",
                (map_slug, vt_id, ip_hash, list(edge_ids), exclude_device_id or ""),
            )
            return {edge_id: cnt for edge_id, cnt in cursor.fetchall()}
    except Exception as e:
        logger.error(f"[DB] Failed to count devices per ip: {e}")
        return {}


def evict_lru_devices_for_edges(
    map_slug: str, edge_ids: list[int], vt_id: int, ip_hash: str,
    new_device_id: str,
) -> dict[int, int]:
    """Transfer one capped vote per edge from this IP's least-recently-active
    device to `new_device_id`, returning {edge_id: direction} for the rows moved.

    The per-IP cap bounds how many distinct devices an IP may hold on an
    edge+type. Once at the cap, a fresh device doesn't add a new vote (which
    would let one IP inflate a total without limit) — instead it takes over the
    oldest device's existing vote. Only the owner (device_id) and updated_at
    change: the direction, count, and migration anchor are untouched, so the
    total never moves. The caller guarantees `new_device_id` holds no row on
    these edges yet, so the ownership move can't collide with the identity key.
    """
    if not DATABASE_URL or not edge_ids or not ip_hash:
        return {}
    try:
        with get_cursor() as cursor:
            cursor.execute(
                """WITH lru AS (
                       SELECT DISTINCT ON (edge_id) id, edge_id
                       FROM edge_votes
                       WHERE map_slug = %s AND vote_type_id = %s AND ip_hash = %s
                         AND edge_id = ANY(%s) AND device_id <> %s
                       ORDER BY edge_id, updated_at ASC NULLS FIRST, id ASC
                   )
                   UPDATE edge_votes ev
                   SET device_id = %s, updated_at = NOW()
                   FROM lru
                   WHERE ev.id = lru.id
                   RETURNING ev.edge_id, ev.direction""",
                (map_slug, vt_id, ip_hash, list(edge_ids), new_device_id,
                 new_device_id),
            )
            return {int(edge_id): int(direction)
                    for edge_id, direction in cursor.fetchall()}
    except Exception as e:
        logger.error(f"[DB] LRU eviction failed: {e}")
        return {}


def get_voter_type_rows(
    map_slug: str, vote_type_id: int, device_id: str
) -> dict[int, int]:
    """Return {edge_id: direction} for ALL of a device's rows of one vote type
    on a map — the prior state the block-scoped vote plan clears against."""
    if not DATABASE_URL:
        return {}
    try:
        with get_cursor() as cursor:
            cursor.execute(
                """SELECT edge_id, direction
                   FROM edge_votes
                   WHERE map_slug = %s AND vote_type_id = %s AND device_id = %s""",
                (map_slug, vote_type_id, device_id),
            )
            return {int(edge_id): int(direction)
                    for edge_id, direction in cursor.fetchall()}
    except Exception as e:
        logger.error(f"[DB] Failed to read voter type rows: {e}")
        return {}


def count_unique_voters_for_edge_sets(
    map_slug: str, edge_sets: list[list[int]]
) -> list[list[tuple[int, int, int]]]:
    """Distinct-voter vote counts for MANY edge sets, in ONE query.

    Same counting rule as `count_unique_voters_for_edges` (which delegates
    here — this is the only place the rule is written), evaluated per input
    set: returns one [(vote_type_id, direction, count)] list per set, aligned
    with `edge_sets` by index.

    The batch exists because the map now asks the question for every corridor
    it labels at once, not just for the one card that is open. Sets are tagged
    with their index and unnested into a two-column relation, so the whole
    batch is a single index scan + GROUP BY rather than one round trip per
    corridor. Duplicate edge ids WITHIN a set are harmless (COUNT DISTINCT
    absorbs the duplicated join rows), and an edge shared BETWEEN sets is
    counted for each — the sets are independent questions.
    """
    results: list[list[tuple[int, int, int]]] = [[] for _ in edge_sets]
    idx_col: list[int] = []
    edge_col: list[int] = []
    for i, edge_ids in enumerate(edge_sets):
        for e in edge_ids:
            idx_col.append(i)
            edge_col.append(int(e))
    if not DATABASE_URL or not edge_col:
        return results
    try:
        with get_cursor() as cursor:
            cursor.execute(
                # The identity expression is spelled by vote_identity and used
                # UNQUALIFIED: it may be a COALESCE over two columns, so a
                # table prefix would land on the function, not the columns.
                # Only edge_votes has those columns, so it is unambiguous.
                f"""SELECT s.idx, v.vote_type_id, v.direction,
                          COUNT(DISTINCT {vote_identity.SQL_IDENTITY})
                   FROM unnest(%s::int[], %s::int[]) AS s(idx, edge_id)
                   JOIN edge_votes v
                     ON v.map_slug = %s AND v.edge_id = s.edge_id
                   GROUP BY s.idx, v.vote_type_id, v.direction""",
                (idx_col, edge_col, map_slug),
            )
            for idx, vt, d, c in cursor.fetchall():
                results[int(idx)].append((int(vt), int(d), int(c)))
            return results
    except Exception as e:
        logger.error(f"[DB] Failed to count unique voters for edge sets: {e}")
        return [[] for _ in edge_sets]


def count_unique_voters_for_edges(
    map_slug: str, edge_ids: list[int]
) -> list[tuple[int, int, int]]:
    """Distinct-voter vote counts over an edge set, per (vote type, direction).

    A voter who cast the same type/direction on many edges of the set (a route
    cast fans one vote across every edge of every block it covers) counts
    ONCE — the honest "how many people voted on this route" number the
    route-summary card shows. Returns [(vote_type_id, direction, count)].

    Counted on the same identity as the block layer (vote_identity.SQL_IDENTITY),
    so the card and the pins can't disagree about how many people that is.
    """
    if not edge_ids:
        return []
    return count_unique_voters_for_edge_sets(map_slug, [list(edge_ids)])[0]


def note_map_open(device_id: str, ip_hash: Optional[str]) -> tuple[bool, Optional[str]]:
    """Record that this person just opened a map — and say whether they had ever
    opened one BEFORE this call.

    The first-run wall hangs off the answer, so the ordering inside is the whole
    function: READ, THEN WRITE, and return what the read said. Recording first
    (or letting the [MAPLOAD] beacon do the recording and reading separately)
    means every visitor's very first probe finds their own current visit already
    on file, answers "yes, you have been here", and nobody is ever welcomed.

    Keyed on the COUNTING identity (vote_identity), the same key and the same
    probe order the vote counter itself uses, for the same reason: a
    browser's `voter_id` fragments — six ids from one iPhone in three minutes on
    2026-08-13 — and a welcome keyed on something that fragments repeats itself
    at the people it should leave alone.

    One deliberate widening of the read: somebody who has ever CAST a vote has
    obviously opened a map, whether or not this table was around when they did.
    Without that clause the day this ships hands a newcomer's wall to every
    veteran voter on the site, because their opens all predate the table.

    Returns (visited_before, by) where `by` is "ip" | "device" | "vote" | None —
    which key answered, so an operator reading a log can tell a returning browser
    from a co-NAT stranger from a backfilled voter.
    """
    if not DATABASE_URL:
        return False, None
    keys: list[tuple[str, str]] = []
    if vote_identity.COUNT_BY == "ip" and ip_hash:
        keys.append(("ip", ip_hash))
    keys.append(("device", device_id))
    try:
        with get_cursor() as cursor:
            before: Optional[str] = None
            for kind, value in keys:
                cursor.execute(
                    "SELECT 1 FROM map_visitors WHERE key_kind = %s AND key_value = %s",
                    (kind, value),
                )
                if cursor.fetchone():
                    before = kind
                    break
            if before is None:
                # Two indexed EXISTS probes on the same two keys, run on THIS
                # cursor rather than through a second get_cursor(): that context
                # holds a pooled connection for its whole body, and nesting one
                # would hold two per request on the page-load path.
                for kind, value in keys:
                    column = "ip_hash" if kind == "ip" else "device_id"
                    cursor.execute(
                        f"SELECT EXISTS (SELECT 1 FROM edge_votes WHERE {column} = %s)",
                        (value,),
                    )
                    if cursor.fetchone()[0]:
                        before = "vote"
                        break
            # Write both keys, always: an identity that arrives on a new network
            # must leave a mark on the device key too, or clearing storage on the
            # old network reads as a brand-new person.
            for kind, value in keys:
                cursor.execute(
                    "INSERT INTO map_visitors (key_kind, key_value) VALUES (%s, %s) "
                    "ON CONFLICT (key_kind, key_value) DO UPDATE "
                    "SET last_seen = NOW(), opens = map_visitors.opens + 1",
                    (kind, value),
                )
            return (before is not None), before
    except Exception as e:
        # Never let this decide anything by failing: the caller treats an error
        # as "returning visitor" (see the client's fail-closed rule).
        logger.warning(f"[DB] note_map_open failed: {e}")
        raise


# ── Vote types (label ↔ id) ─────────────────────────────────────────────────

def fetch_all_vote_types() -> list[tuple[int, str]]:
    """All (id, label) vote types, ordered by id. Backs the in-memory cache."""
    if not DATABASE_URL:
        return []
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT id, label FROM vote_types ORDER BY id")
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"[DB] Failed to fetch vote types: {e}")
        return []


def fetch_vote_type_label(vt_id: int) -> Optional[str]:
    """One vote type's label, or None if no such row. Backs the cache-miss path
    in vote_store.resolve_vote_type — an id this process has never seen is
    usually a label another instance minted, not an orphan."""
    if not DATABASE_URL or not vt_id:
        return None
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT label FROM vote_types WHERE id = %s", (vt_id,))
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error(f"[DB] Failed to fetch vote type #{vt_id}: {e}")
        return None


def fetch_voted_vote_type_labels(map_slug: str) -> list[dict]:
    """Distinct vote types actually voted on a map (newest activity first), each
    as {"label", "pointType"} — pointType is 'route' | 'point' | None (unknown).

    Backs the selector's *search-only* suggestions: a custom vote type surfaces
    when searched for once someone has voted it, but never joins the default
    suggestion list. Excludes the blank/default type (vote_type_id 0)."""
    if not DATABASE_URL or not map_slug:
        return []
    try:
        with get_cursor() as cursor:
            cursor.execute(
                """SELECT vt.label, vt.point_type
                     FROM edge_votes ev
                     JOIN vote_types vt ON vt.id = ev.vote_type_id
                    WHERE ev.map_slug = %s AND ev.vote_type_id <> 0
                    GROUP BY vt.label, vt.point_type
                    ORDER BY MAX(ev.created_at) DESC""",
                (map_slug,),
            )
            return [
                {"label": r[0], "pointType": r[1]}
                for r in cursor.fetchall() if r[0]
            ]
    except Exception as e:
        logger.error(f"[DB] Failed to fetch voted vote types for '{map_slug}': {e}")
        return []


def normalize_point_type(value) -> Optional[str]:
    """'route' / 'point' pass through; anything else (including None) → None."""
    return value if value in ("route", "point") else None


def get_or_create_vote_type_id(label: str, point_type: Optional[str] = None) -> int:
    """Return the id for a vote-type label, creating the row if needed.

    `point_type` ('route' | 'point') stamps a NEW row's kind and fills a NULL on
    an existing row — it never overwrites a kind already on record, so the first
    authoritative flag (preset seed, map creation, or the creating cast) wins.

    Raises VoteTypeLimitError when the minted id is past what the packed vote-key
    layouts can address (vote_store.MAX_VOTE_TYPE_ID). vote_types.id is an int4
    SERIAL, 128x wider than the 24-bit field the Redis vote key, the block
    aggregate key and the client's store key all carry it in — and an id past
    that ceiling does not fail, it overflows onto a DIRECTION bit and records
    upvotes as downvotes. That happened once already at 16 bits. Refusing here
    means a human sees an error at the moment they name a new type, instead of
    the votes going quietly the wrong way afterwards."""
    import vote_store  # local: vote_store imports this module lazily too

    if not DATABASE_URL or not label:
        return 0
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO vote_types (label, point_type) VALUES (%s, %s) "
                "ON CONFLICT (label) DO UPDATE SET point_type = "
                "COALESCE(vote_types.point_type, EXCLUDED.point_type) RETURNING id",
                (label, normalize_point_type(point_type)),
            )
            vt_id = cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"[DB] Failed to get/create vote type '{label}': {e}")
        return 0
    # Checked OUTSIDE the try so the generic handler above cannot turn a hard
    # limit into a silent `return 0` — a 0 id reads as "untyped vote" downstream,
    # which is precisely the quiet wrong answer this guard exists to prevent.
    if vt_id > vote_store.MAX_VOTE_TYPE_ID:
        logger.error(
            f"[DB] vote type '{label}' minted id {vt_id}, past the "
            f"{vote_store.VT_BITS}-bit vote-key limit "
            f"({vote_store.MAX_VOTE_TYPE_ID})")
        raise vote_store.VoteTypeLimitError(
            f"Cannot create the vote type “{label}”: this map service supports "
            f"at most {vote_store.MAX_VOTE_TYPE_ID:,} distinct vote types and "
            f"that limit has been reached (id {vt_id:,}). Existing vote types "
            f"still work — please pick one of them, and let the maintainers "
            f"know the vote-type id space needs widening.")
    return vt_id


# ── Aggregates / admin (replaces inline SQL elsewhere) ───────────────────────

def aggregate_votes_for_replay(map_slug: Optional[str] = None) -> list[tuple[str, int, int, int, int]]:
    """Per (map_slug, edge_id, vote_type_id, direction) vote counts, for Redis replay.

    Pass map_slug to replay a single map (used by the vote migration after a resnap).
    """
    if not DATABASE_URL:
        return []
    try:
        with get_cursor() as cursor:
            if map_slug is None:
                cursor.execute("""
                    SELECT map_slug, edge_id, vote_type_id, direction, COUNT(*)
                    FROM edge_votes
                    GROUP BY map_slug, edge_id, vote_type_id, direction
                """)
            else:
                cursor.execute("""
                    SELECT map_slug, edge_id, vote_type_id, direction, COUNT(*)
                    FROM edge_votes WHERE map_slug = %s
                    GROUP BY map_slug, edge_id, vote_type_id, direction
                """, (map_slug,))
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"[DB] Failed to aggregate votes: {e}")
        return []


def fetch_edge_vote_identities(map_slug: str) -> list[tuple[int, int, int, str, str]]:
    """Identity-bearing edge votes for one map: (edge_id, vote_type_id,
    direction, device_id, ip_hash). Unlike aggregate_votes_for_replay (which
    discards identity), this keeps both so block_votes.rebuild_from_db can
    reconstruct the per-block dedup: it counts on the counting identity and
    checks the one-direction invariant on the device. One row per stored vote
    (already unique per edge/type/device)."""
    if not DATABASE_URL:
        return []
    try:
        with get_cursor() as cursor:
            cursor.execute("""
                SELECT edge_id, vote_type_id, direction, device_id, ip_hash
                FROM edge_votes WHERE map_slug = %s
            """, (map_slug,))
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"[DB] Failed to fetch edge vote identities for '{map_slug}': {e}")
        return []


# ── Vote migration (graph rebuild → re-snap anchors to current edge ids) ─────

def resnap_edge_votes(map_slug: str, pairs: list[tuple[int, int]]) -> dict:
    """Re-point votes at new edge ids after a graph rebuild, deduping collisions.

    `pairs` is [(row_id, new_edge_id)]. Done in ONE transaction on a dedicated
    connection (the shared one is autocommit) so it's atomic — votes are never
    left deleted-but-not-reinserted. Dedup avoids per-id double-voting: when
    several old edges collapse onto the same new edge for the same
    (map, vote_type, device), only one survives (lowest row id). Idempotent: the
    lat/lon anchor is preserved, so re-running re-snaps to the same edge.
    """
    if not DATABASE_URL or not pairs:
        return {"rows": 0, "kept": 0, "merged": 0}

    conn = psycopg2.connect(DATABASE_URL)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE _resnap (id INT PRIMARY KEY, new_edge INT) ON COMMIT DROP"
            )
            execute_values(cur, "INSERT INTO _resnap (id, new_edge) VALUES %s", pairs)

            cur.execute("SELECT COUNT(*) FROM edge_votes WHERE map_slug = %s", (map_slug,))
            before = cur.fetchone()[0]

            # One survivor per target identity (lowest id wins), carrying its
            # attributes + the new edge. Then delete the migrated rows and reinsert
            # the deduped survivors — delete+reinsert sidesteps unique-constraint
            # churn that an in-place UPDATE that permutes edge ids would hit.
            cur.execute("""
                CREATE TEMP TABLE _staged ON COMMIT DROP AS
                SELECT DISTINCT ON (ev.map_slug, r.new_edge, ev.vote_type_id, ev.device_id)
                       ev.map_slug, r.new_edge AS edge_id, ev.vote_type_id, ev.device_id,
                       ev.ip_hash, ev.direction, ev.created_at, ev.lat, ev.lon
                FROM edge_votes ev JOIN _resnap r ON r.id = ev.id
                ORDER BY ev.map_slug, r.new_edge, ev.vote_type_id, ev.device_id, ev.id
            """)
            cur.execute("DELETE FROM edge_votes ev USING _resnap r WHERE ev.id = r.id")
            cur.execute("""
                INSERT INTO edge_votes
                    (map_slug, edge_id, vote_type_id, device_id, ip_hash, direction, created_at, lat, lon)
                SELECT map_slug, edge_id, vote_type_id, device_id, ip_hash, direction, created_at, lat, lon
                FROM _staged
                ON CONFLICT (map_slug, edge_id, vote_type_id, device_id) DO NOTHING
            """)

            cur.execute("SELECT COUNT(*) FROM edge_votes WHERE map_slug = %s", (map_slug,))
            after = cur.fetchone()[0]
        conn.commit()
        return {"rows": before, "kept": after, "merged": before - after}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def orphaned_vote_type_counts(map_slug: str) -> list[tuple[int, int]]:
    """[(vote_type_id, rows)] for a map's votes whose type id has no vote_types row,
    most-voted first. These render as '#<id>' in the legend until repaired."""
    if not DATABASE_URL:
        return []
    try:
        with get_cursor() as cursor:
            cursor.execute("""
                SELECT ev.vote_type_id, COUNT(*)
                FROM edge_votes ev
                LEFT JOIN vote_types vt ON vt.id = ev.vote_type_id
                WHERE ev.map_slug = %s AND ev.vote_type_id <> 0 AND vt.id IS NULL
                GROUP BY ev.vote_type_id
                ORDER BY COUNT(*) DESC
            """, (map_slug,))
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"[DB] Failed to read orphaned vote types: {e}")
        return []


def count_map_votes_by_type(map_slug: str, vt_id: int) -> int:
    """How many of a map's edge_votes rows carry one vote type. The dry-run half
    of a retype (retype_votes.py): what remap_edge_vote_type would touch."""
    if not DATABASE_URL:
        return 0
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM edge_votes WHERE map_slug = %s AND vote_type_id = %s",
                (map_slug, vt_id),
            )
            return int(cursor.fetchone()[0])
    except Exception as e:
        logger.error(f"[DB] Failed to count votes of type {vt_id} on '{map_slug}': {e}")
        return 0


def remap_edge_vote_type(map_slug: str, old_vt_id: int, new_vt_id: int) -> int:
    """Re-point a map's votes from one vote_type_id to another; returns rows changed.

    Collision-safe: if the target id already has a vote by the same device on the
    same edge (e.g. an orphan repaired onto an existing label), the duplicate is
    dropped before the update so the unique identity constraint can't fail.
    """
    if not DATABASE_URL or old_vt_id == new_vt_id:
        return 0
    try:
        with get_cursor() as cursor:
            # Drop would-be duplicates (device already voted target type on that edge).
            cursor.execute("""
                DELETE FROM edge_votes ev
                WHERE ev.map_slug = %s AND ev.vote_type_id = %s
                  AND EXISTS (
                    SELECT 1 FROM edge_votes o
                    WHERE o.map_slug = ev.map_slug AND o.edge_id = ev.edge_id
                      AND o.vote_type_id = %s AND o.device_id = ev.device_id
                  )
            """, (map_slug, old_vt_id, new_vt_id))
            cursor.execute(
                "UPDATE edge_votes SET vote_type_id = %s WHERE map_slug = %s AND vote_type_id = %s",
                (new_vt_id, map_slug, old_vt_id),
            )
            return cursor.rowcount
    except Exception as e:
        logger.error(f"[DB] Failed to remap vote type {old_vt_id}->{new_vt_id}: {e}")
        return 0


def get_admin_counts() -> dict:
    """Row counts for the admin stats endpoint."""
    if not DATABASE_URL:
        return {}
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM edge_votes")
            ev = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM vote_types")
            vt = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM maps")
            mp = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(DISTINCT device_id) FROM edge_votes")
            voters = cursor.fetchone()[0]
        return {"edge_votes": ev, "vote_types": vt, "maps": mp, "unique_voters": voters}
    except Exception as e:
        return {"error": str(e)}


# ── Maps & vote-type lists ───────────────────────────────────────────────────

def seed_presets():
    """Seed preset vote-type lists and the preset NYC maps (idempotent)."""
    if not DATABASE_URL:
        return
    import json as _json
    from presets import PRESET_LISTS, PRESET_MAPS

    try:
        with get_cursor() as cursor:
            list_ids: dict[str, int] = {}
            for key, spec in PRESET_LISTS.items():
                cursor.execute(
                    """INSERT INTO vote_type_lists (key, name, is_preset, featured, vote_types)
                       VALUES (%s, %s, TRUE, TRUE, %s)
                       ON CONFLICT (key) DO UPDATE
                         SET name = EXCLUDED.name, vote_types = EXCLUDED.vote_types,
                             featured = TRUE
                       RETURNING id""",
                    (key, spec["name"], _json.dumps(spec["vote_types"])),
                )
                list_ids[key] = cursor.fetchone()[0]

            for m in PRESET_MAPS:
                cursor.execute(
                    """INSERT INTO maps
                         (slug, name, subtitle, city_id, vote_type_list_id,
                          allow_suggestions, subdomain)
                       VALUES (%s, %s, %s, %s, %s, TRUE, %s)
                       ON CONFLICT (slug) DO UPDATE
                         SET name = EXCLUDED.name, subtitle = EXCLUDED.subtitle,
                             city_id = EXCLUDED.city_id,
                             vote_type_list_id = EXCLUDED.vote_type_list_id,
                             subdomain = EXCLUDED.subdomain""",
                    (m["slug"], m["name"], m.get("subtitle"), m["city_id"],
                     list_ids.get(m["list_key"]), m["subdomain"]),
                )

            # Register every preset label in the global vote_types id registry.
            # Votes pack this integer id into their Redis key; the heatmap legend
            # resolves id → label through this table. Seeding the canonical labels
            # here (idempotent on the unique label) guarantees a preset type
            # always resolves to its name instead of falling back to "#<id>" —
            # e.g. after a DB reset that renumbered the registry while Redis still
            # holds the old packed ids. Existing labels keep their ids, but the
            # preset's route/point kind is authoritative and overwrites — a
            # preset label that predates the point_type column (or was mis-
            # flagged) gets repaired on every startup.
            for spec in PRESET_LISTS.values():
                for vt in spec["vote_types"]:
                    label = vt.get("label")
                    if label:
                        cursor.execute(
                            "INSERT INTO vote_types (label, point_type) "
                            "VALUES (%s, %s) ON CONFLICT (label) DO UPDATE "
                            "SET point_type = EXCLUDED.point_type",
                            (label, normalize_point_type(vt.get("pointType"))),
                        )
        logger.info("[DB] Seeded preset vote-type lists and maps")
    except Exception as e:
        logger.error(f"[DB] Failed to seed presets: {e}")


def backfill_vote_type_kinds() -> int:
    """Repair vote_types rows with an unknown route/point kind (point_type NULL).

    Every authored vote-type set already declares each entry's kind — the preset
    lists, promoted community lists (vote_type_lists.vote_types JSONB), and each
    map's inline custom_vote_types. Only the global vote_types registry predates
    the flag, so NULL rows there are filled by label match against every authored
    set. Rows still NULL afterwards are true free-text suggestions cast before
    kinds were recorded; they stay NULL (clients treat unknown as either kind).
    Idempotent — runs on every startup after seed_presets(). Returns the number
    of rows repaired."""
    if not DATABASE_URL:
        return 0
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT vote_types FROM vote_type_lists")
            authored = [r[0] for r in cursor.fetchall()]
            cursor.execute(
                "SELECT custom_vote_types FROM maps WHERE custom_vote_types IS NOT NULL"
            )
            authored += [r[0] for r in cursor.fetchall()]

            # First authored kind per label wins (presets sort ahead only by
            # insertion; conflicts across sets are rare and arbitrary anyway).
            kind_by_label: dict[str, str] = {}
            for vts in authored:
                for vt in vts or []:
                    label = (vt.get("label") or "").strip()
                    kind = normalize_point_type(vt.get("pointType"))
                    if label and kind and label not in kind_by_label:
                        kind_by_label[label] = kind

            repaired = 0
            for label, kind in kind_by_label.items():
                cursor.execute(
                    "UPDATE vote_types SET point_type = %s "
                    "WHERE label = %s AND point_type IS NULL",
                    (kind, label),
                )
                repaired += cursor.rowcount
            if repaired:
                logger.info(f"[DB] Backfilled point_type on {repaired} vote type(s)")
            return repaired
    except Exception as e:
        logger.error(f"[DB] Failed to backfill vote-type kinds: {e}")
        return 0


def list_vote_type_lists() -> list[dict]:
    """Return the featured vote-type lists shown in the Propose-a-Map picker.
    Featured = the curated set offered to map proposers (system presets plus any
    promoted community sets); see promote_vote_types()."""
    if not DATABASE_URL:
        return []
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT id, key, name, is_preset, vote_types "
                "FROM vote_type_lists WHERE featured = TRUE ORDER BY is_preset DESC, id"
            )
            return [
                {"id": r[0], "key": r[1], "name": r[2], "isPreset": r[3], "voteTypes": r[4]}
                for r in cursor.fetchall()
            ]
    except Exception as e:
        logger.error(f"[DB] Failed to list vote-type lists: {e}")
        return []


def promote_vote_types(slug: str, name: Optional[str] = None) -> tuple[bool, str]:
    """Copy a map's resolved vote-type set into a featured vote_type_lists row so
    it appears as a selectable option in the Propose-a-Map picker. This is the
    "I like this community set, add it to the picker" path: the map's inline
    custom_vote_types (or its linked list) are snapshotted into a reusable list.

    Idempotent by the generated key (``m:<slug>``): re-promoting the same map
    refreshes that list's name/vote types rather than creating duplicates."""
    if not DATABASE_URL:
        return False, "Database not configured"
    import json as _json
    try:
        with get_cursor() as cursor:
            cursor.execute(
                """SELECT m.name, m.custom_vote_types, vtl.vote_types
                   FROM maps m
                   LEFT JOIN vote_type_lists vtl ON vtl.id = m.vote_type_list_id
                   WHERE m.slug = %s""",
                (slug,),
            )
            row = cursor.fetchone()
            if not row:
                return False, f"Map '{slug}' not found"
            map_name, custom_vote_types, list_vote_types = row
            vote_types = custom_vote_types or list_vote_types
            if not vote_types:
                return False, "Map has no vote types to promote"
            cursor.execute(
                """INSERT INTO vote_type_lists (key, name, is_preset, featured, vote_types)
                   VALUES (%s, %s, FALSE, TRUE, %s)
                   ON CONFLICT (key) DO UPDATE
                     SET name = EXCLUDED.name, featured = TRUE,
                         vote_types = EXCLUDED.vote_types
                   RETURNING id""",
                (f"m:{slug}", (name or "").strip() or map_name, _json.dumps(vote_types)),
            )
            list_id = cursor.fetchone()[0]
        return True, f"Promoted to featured vote-type list #{list_id}"
    except Exception as e:
        logger.error(f"[DB] Failed to promote vote types for {slug}: {e}")
        return False, "Failed to promote vote types"


# Preset subdomains double as the packed-key "mode" string and the client style
# key. User maps (no subdomain) share the neutral "walk" mode + "default" style;
# each map's votes are isolated by slug, so a shared mode is safe.
_PRESET_STYLES = {"bikepaths", "trees", "walkways"}


def _map_row_to_dict(row) -> dict:
    """Shape a maps JOIN vote_type_lists row into the public map dict."""
    (slug, name, subtitle, city_id, allow_suggestions, has_passcode,
     subdomain, list_vote_types, custom_vote_types, vote_count, symbol, style,
     network, top_proposal_min_net) = row
    vote_types = custom_vote_types or list_vote_types or []
    # The proposer-chosen visual style wins; presets keep their subdomain style;
    # everything else falls back to the neutral default. The vote namespace
    # (mode) follows the resolved style — not the subdomain — so a map can live
    # in a preset namespace (e.g. "bikepaths") without owning the globally-unique
    # preset subdomain. Presets are unaffected: their style already == subdomain.
    resolved_style = style or (subdomain if subdomain in _PRESET_STYLES else "default")
    return {
        "slug": slug,
        "name": name,
        "subtitle": subtitle or "",
        "cityId": city_id,
        "allowSuggestions": allow_suggestions,
        "requiresPasscode": has_passcode,
        "subdomain": subdomain,
        "mode": resolved_style if resolved_style in _PRESET_STYLES else "walk",
        "style": resolved_style,
        "network": network or "streets",
        "voteCount": int(vote_count or 0),
        "voteTypes": vote_types,
        # The map's display icon for the landing card. Falls back to the first
        # vote type's icon when the proposer didn't pick one explicitly.
        "symbol": symbol or (vote_types[0].get("icon") if vote_types else "") or "",
        # Only present when the row overrides the client's default floor.
        **({"topProposalMinNet": top_proposal_min_net}
           if top_proposal_min_net is not None else {}),
    }


# Three variants of the same column list, differing only in how vote_count is
# produced. The full-table GROUP BY over edge_votes is only affordable when the
# caller genuinely needs every map's count (list_maps ranks by it); running it
# per single-map lookup put a ~100ms-3s Postgres aggregate under EVERY API
# request via resolve_map, which was the app-wide latency floor.
_MAP_COLUMNS = """
    SELECT m.slug, m.name, m.subtitle, m.city_id, m.allow_suggestions,
           (m.passcode_hash IS NOT NULL) AS has_passcode,
           m.subdomain, vtl.vote_types, m.custom_vote_types,
           {vote_count} AS vote_count, m.symbol, m.style, m.network,
           m.top_proposal_min_net
    FROM maps m
    LEFT JOIN vote_type_lists vtl ON vtl.id = m.vote_type_list_id
"""

# All maps' counts at once (list_maps ranking).
_MAP_SELECT = _MAP_COLUMNS.format(vote_count="COALESCE(vc.cnt, 0)") + """
    LEFT JOIN (SELECT map_slug, COUNT(*) AS cnt FROM edge_votes GROUP BY map_slug) vc
      ON vc.map_slug = m.slug
"""

# One map's count via idx_edge_votes_map — an index scan over just that map's
# rows instead of an aggregate over the whole table.
_MAP_SELECT_ONE = _MAP_COLUMNS.format(
    vote_count="(SELECT COUNT(*) FROM edge_votes ev WHERE ev.map_slug = m.slug)"
)

# No count at all (vote_count = 0) — for resolve_map, which never reads it.
_MAP_SELECT_LIGHT = _MAP_COLUMNS.format(vote_count="0")


def list_maps() -> list[dict]:
    """All maps, ranked by total votes (desc) then name (asc)."""
    if not DATABASE_URL:
        return []
    try:
        with get_cursor() as cursor:
            cursor.execute(_MAP_SELECT + " ORDER BY vote_count DESC, LOWER(m.name) ASC")
            return [_map_row_to_dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.error(f"[DB] Failed to list maps: {e}")
        return []


def get_map(slug: str, with_vote_count: bool = True) -> Optional[dict]:
    """One map by slug. Pass with_vote_count=False on hot paths that never
    read voteCount (resolve_map) — it skips the edge_votes count entirely."""
    if not DATABASE_URL:
        return None
    select = _MAP_SELECT_ONE if with_vote_count else _MAP_SELECT_LIGHT
    try:
        with get_cursor() as cursor:
            cursor.execute(select + " WHERE m.slug = %s", (slug,))
            row = cursor.fetchone()
            return _map_row_to_dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Failed to get map '{slug}': {e}")
        return None


def get_map_by_subdomain(subdomain: str) -> Optional[dict]:
    if not DATABASE_URL:
        return None
    try:
        with get_cursor() as cursor:
            cursor.execute(_MAP_SELECT_ONE + " WHERE m.subdomain = %s LIMIT 1", (subdomain,))
            row = cursor.fetchone()
            return _map_row_to_dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Failed to get map by subdomain '{subdomain}': {e}")
        return None


def set_map_subdomain(slug: str, subdomain: Optional[str]) -> tuple[bool, str]:
    """Attach (or clear) a vanity subdomain on a map.

    `subdomain=None`/"" clears it. Subdomains are unique across maps (enforced by
    idx_maps_subdomain); this also checks first so callers get a clear message
    instead of an integrity error. Returns (ok, message).
    """
    if not DATABASE_URL:
        return False, "database unavailable"
    sub = (subdomain or "").strip().lower() or None
    try:
        with get_cursor() as cursor:
            if sub:
                cursor.execute(
                    "SELECT slug FROM maps WHERE subdomain = %s AND slug <> %s",
                    (sub, slug),
                )
                taken = cursor.fetchone()
                if taken:
                    return False, f"subdomain '{sub}' already used by map '{taken[0]}'"
            cursor.execute(
                "UPDATE maps SET subdomain = %s WHERE slug = %s", (sub, slug)
            )
            if cursor.rowcount == 0:
                return False, f"no map with slug '{slug}'"
        return True, ("cleared" if sub is None else f"set to '{sub}'")
    except Exception as e:
        logger.error(f"[DB] set_map_subdomain failed: {e}")
        return False, str(e)


def set_vote_sources(
    slug: str, sources: list[dict], replace: bool = True
) -> tuple[bool, str]:
    """Register a map's imported-vote source registry.

    Each entry is {"device_id", "url", "title"?} — device_id being the hashed
    voter id the import's casts carry on their edge_votes rows. With
    `replace` (the default) the whole registry for the map is replaced
    atomically — callers send the full desired state, and an empty list clears
    it. With `replace=False` the given entries are merged into what's already
    there, which is what an incremental importer needs: it only knows the
    proposals it just cast, and a wholesale write would strand every earlier
    one. Returns (ok, message).
    """
    if not DATABASE_URL:
        return False, "database unavailable"
    rows = []
    for s in sources or []:
        device_id, url = s.get("device_id"), s.get("url")
        if not device_id or not isinstance(url, str) or not url:
            return False, f"invalid source entry: {s!r}"
        rows.append((slug, str(device_id)[:16], url, s.get("title") or None))
    try:
        with get_cursor() as cursor:
            if replace:
                cursor.execute(
                    "DELETE FROM vote_sources WHERE map_slug = %s", (slug,))
            if rows:
                execute_values(
                    cursor,
                    "INSERT INTO vote_sources (map_slug, device_id, url, title) "
                    "VALUES %s ON CONFLICT (map_slug, device_id) DO UPDATE "
                    "SET url = EXCLUDED.url, title = EXCLUDED.title",
                    rows,
                )
        return True, (f"registered {len(rows)} sources"
                      + ("" if replace else " (merged)"))
    except Exception as e:
        logger.error(f"[DB] set_vote_sources failed: {e}")
        return False, str(e)


def fetch_vote_sources_for_edges(
    map_slug: str, edge_ids: list[int]
) -> list[tuple[str, str, Optional[str], int]]:
    """Where the votes ON these edges came from, as (label, url, title, edges).

    Joins the live votes to the source registry, so a card shows the actual
    proposals that produced the votes under it — not every proposal of the same
    type elsewhere in the city. `edges` is how many of the queried edges that
    source voted on, so the dominant proposal for a block sorts first. Only
    upvotes count: a cancelled/countered import shouldn't cite its source.
    """
    if not DATABASE_URL or not edge_ids:
        return []
    try:
        with get_cursor() as cursor:
            cursor.execute(
                """SELECT vt.label, vs.url, vs.title, COUNT(DISTINCT ev.edge_id)
                     FROM edge_votes ev
                     JOIN vote_sources vs
                       ON vs.map_slug = ev.map_slug AND vs.device_id = ev.device_id
                     JOIN vote_types vt ON vt.id = ev.vote_type_id
                    WHERE ev.map_slug = %s AND ev.edge_id = ANY(%s)
                      AND ev.direction > 0
                    GROUP BY vt.label, vs.url, vs.title
                    ORDER BY COUNT(DISTINCT ev.edge_id) DESC, vs.url""",
                (map_slug, list(edge_ids)),
            )
            return [(r[0], r[1], r[2], int(r[3])) for r in cursor.fetchall() if r[0]]
    except Exception as e:
        logger.error(f"[DB] fetch_vote_sources_for_edges failed: {e}")
        return []


def slug_available(slug: str) -> bool:
    """A slug is takeable only if no map uses it AND no redirect reserves it —
    a renamed map's old slug must never be re-claimed (printed QR links would
    suddenly land on a stranger's map)."""
    if not DATABASE_URL:
        return False
    try:
        with get_cursor() as cursor:
            cursor.execute(
                """SELECT 1 FROM maps WHERE slug = %s
                   UNION ALL
                   SELECT 1 FROM map_redirects WHERE from_slug = %s""",
                (slug, slug),
            )
            return cursor.fetchone() is None
    except Exception as e:
        logger.error(f"[DB] slug_available check failed: {e}")
        return False


# ── Slug redirects (renamed maps) ───────────────────────────────────────────

def get_map_redirect(from_slug: str) -> Optional[dict]:
    """The redirect for a retired slug, or None. Shape mirrors the client's
    MapConfig.redirect field: {"toSlug", "appendQuery"}."""
    if not DATABASE_URL:
        return None
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT to_slug, append_query FROM map_redirects WHERE from_slug = %s",
                (from_slug,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {"toSlug": row[0], "appendQuery": row[1] or None}
    except Exception as e:
        logger.error(f"[DB] get_map_redirect '{from_slug}' failed: {e}")
        return None


def list_map_redirects() -> list[dict]:
    """All slug redirects (admin/docs transparency)."""
    if not DATABASE_URL:
        return []
    try:
        with get_cursor() as cursor:
            cursor.execute(
                """SELECT from_slug, to_slug, append_query, note, created_at
                   FROM map_redirects ORDER BY created_at"""
            )
            return [
                {"fromSlug": r[0], "toSlug": r[1], "appendQuery": r[2],
                 "note": r[3], "createdAt": r[4].isoformat() if r[4] else None}
                for r in cursor.fetchall()
            ]
    except Exception as e:
        logger.error(f"[DB] list_map_redirects failed: {e}")
        return []


# ── Sticker codes (printed QR stickers) ─────────────────────────────────────

# Codes are minted from a 31-character alphabet with the ambiguous glyphs
# removed (see tools/stickers/codes.py). Anything else never existed on paper.
_STICKER_CODE = re.compile(r"^[23456789abcdefghjkmnpqrstuvwxyz]{4,12}$")


def normalize_sticker_code(code: str) -> Optional[str]:
    """Canonical form of a scanned code, or None if it could not be one.

    The QR carries the URL in uppercase — that is what lets the whole link fit
    QR's alphanumeric mode and stay a small, dense code (tools/stickers/codes.py
    explains the trade) — so every lookup arrives shouting and is folded back
    down here. Validating the shape before it reaches SQL also means a scanner
    that appends junk gets a clean 404 instead of a query.
    """
    if not code:
        return None
    folded = code.strip().lower()
    return folded if _STICKER_CODE.match(folded) else None


def get_sticker(code: str, count_scan: bool = False) -> Optional[dict]:
    """What a scan of `code` should do, or None if no such sticker was printed.

    `count_scan` bumps the scan counter in the same statement as the read, so a
    scan costs one round trip. Per-sticker scan counts live here rather than in
    the map-load metric on purpose: the metric's `src` label is a low-cardinality
    dashboard dimension shared by every sticker carrying the same message, while
    "which individual poles get scanned" is a question with thousands of answers
    and therefore a question for SQL.
    """
    if not DATABASE_URL:
        return None
    norm = normalize_sticker_code(code)
    if not norm:
        return None
    try:
        with get_cursor() as cursor:
            columns = """code, map_slug, vote_type, headline, src_tag,
                         lat, lon, resolved_at,
                         resolved_map_slug, resolved_vote_type, resolved_w"""
            if count_scan:
                cursor.execute(
                    f"""UPDATE sticker_codes
                        SET scans = scans + 1,
                            first_scan_at = COALESCE(first_scan_at, NOW()),
                            last_scan_at = NOW()
                        WHERE code = %s
                        RETURNING {columns}""",
                    (norm,),
                )
            else:
                cursor.execute(
                    f"SELECT {columns} FROM sticker_codes WHERE code = %s", (norm,)
                )
            row = cursor.fetchone()
            if not row:
                return None
            resolved = row[7] is not None
            return {
                "code": row[0],
                # Once resolved, the vote that was actually cast wins over what
                # was printed. A scanner who changed map or picked a different
                # vote type was making a decision, not a mistake, and every
                # later scan should land on the thing they decided.
                "mapSlug": (row[8] if resolved and row[8] else row[1]),
                "voteType": (row[9] if resolved and row[9] else row[2]),
                "headline": row[3],
                "src": row[4],
                # Absent rather than null when unresolved: the client's only
                # question is "do I know where this sticker is yet?".
                "location": ({"lat": row[5], "lng": row[6]}
                             if resolved and row[5] is not None else None),
                # The whole selection, canonical `w=` form. Null for rows
                # resolved before this column existed — the point still works.
                "waypoints": (row[10] if resolved else None),
            }
    except Exception as e:
        logger.error(f"[DB] get_sticker '{code}' failed: {e}")
        return None


def resolve_sticker(code: str, lat: float, lon: float,
                    device_id: Optional[str] = None,
                    map_slug: Optional[str] = None,
                    vote_type: Optional[str] = None,
                    w: Optional[str] = None) -> Optional[dict]:
    """Bind a code to the proposal it turned out to mean, once.

    Called after the first scanner actually casts a vote — not when they merely
    share their location, because a shared location is a claim and a cast vote
    is a commitment. From then on the code opens straight to that proposal.

    What gets bound is the whole vote, not a pin: the map it landed on (which
    may not be the map that was printed, since the scanner can change map before
    voting), the vote type actually cast, and the full ordered selection. `lat`
    and `lon` stay as the first waypoint so the existing "where are the poles?"
    queries keep working on rows written either side of this change.

    Write-once, enforced in the WHERE clause rather than by reading first: two
    people scanning the same fresh code within a second of each other would both
    see it unresolved, and the loser of that race must not overwrite the winner.
    Returns what is now on the row — the existing binding if we lost, which is
    exactly what the caller should honour.
    """
    if not DATABASE_URL:
        return None
    norm = normalize_sticker_code(code)
    if not norm:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    try:
        with get_cursor() as cursor:
            cursor.execute(
                """UPDATE sticker_codes
                   SET lat = %s, lon = %s, resolved_at = NOW(), resolved_by = %s,
                       resolved_map_slug = %s, resolved_vote_type = %s,
                       resolved_w = %s
                   WHERE code = %s AND resolved_at IS NULL""",
                (lat, lon, (device_id or None), (map_slug or None),
                 (vote_type or None), (w or None), norm),
            )
            claimed = cursor.rowcount == 1
            cursor.execute(
                """SELECT lat, lon, resolved_map_slug, resolved_vote_type, resolved_w
                   FROM sticker_codes WHERE code = %s""",
                (norm,),
            )
            row = cursor.fetchone()
            if not row or row[0] is None:
                return None
            logger.info(
                f"[STICKER] resolve code={norm} map={row[2]} vt={row[3]!r} "
                f"w={row[4]} {'claimed' if claimed else 'already-resolved'}"
            )
            return {
                "lat": row[0], "lng": row[1], "claimed": claimed,
                "mapSlug": row[2], "voteType": row[3], "waypoints": row[4],
            }
    except Exception as e:
        logger.error(f"[DB] resolve_sticker '{code}' failed: {e}")
        return None


def rename_map_slug(
    old_slug: str, new_slug: str,
    append_query: Optional[str] = None, note: Optional[str] = None,
) -> dict:
    """Rename a map's slug, atomically.

    One transaction: maps.slug, the denormalized edge_votes.map_slug copies
    (plain TEXT, nothing cascades), and a map_redirects row so the old slug
    keeps resolving AND stays reserved. Raises on any failure (whole txn rolls
    back). Caller must rebuild the map's Redis aggregate under the new slug —
    the heatmap serves from Redis only (see rename_map.py).
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    with get_cursor() as cursor:
        # get_cursor is autocommit — an explicit BEGIN/COMMIT makes the rename
        # atomic (a half-applied rename would strand votes under a dead slug).
        cursor.execute("BEGIN")
        try:
            cursor.execute("SELECT 1 FROM maps WHERE slug = %s", (old_slug,))
            if not cursor.fetchone():
                raise ValueError(f"no map with slug '{old_slug}'")
            cursor.execute(
                """SELECT 1 FROM maps WHERE slug = %s
                   UNION ALL SELECT 1 FROM map_redirects WHERE from_slug = %s""",
                (new_slug, new_slug),
            )
            if cursor.fetchone():
                raise ValueError(f"slug '{new_slug}' is already taken or reserved")
            cursor.execute(
                "UPDATE maps SET slug = %s WHERE slug = %s", (new_slug, old_slug)
            )
            cursor.execute(
                "UPDATE edge_votes SET map_slug = %s WHERE map_slug = %s",
                (new_slug, old_slug),
            )
            votes_moved = cursor.rowcount
            # Re-point any redirect that targeted the old slug (chain
            # flattening: a→b then b→c becomes a→c, so clients never hop twice).
            cursor.execute(
                "UPDATE map_redirects SET to_slug = %s WHERE to_slug = %s",
                (new_slug, old_slug),
            )
            cursor.execute(
                """INSERT INTO map_redirects (from_slug, to_slug, append_query, note)
                   VALUES (%s, %s, %s, %s)""",
                (old_slug, new_slug, append_query or None, note or None),
            )
            cursor.execute("COMMIT")
        except Exception:
            cursor.execute("ROLLBACK")
            raise
    return {"old": old_slug, "new": new_slug, "votesMoved": votes_moved}


def create_map(
    slug: str, name: str, city_id: str,
    subtitle: Optional[str] = None,
    vote_type_list_id: Optional[int] = None,
    custom_vote_types: Optional[list] = None,
    allow_suggestions: bool = True,
    passcode_hash: Optional[str] = None,
    created_by_ip_hash: Optional[str] = None,
    symbol: Optional[str] = None,
    style: Optional[str] = None,
    network: Optional[str] = None,
) -> Optional[dict]:
    """Insert a new map. Returns the public map dict, or None on failure."""
    if not DATABASE_URL:
        return None
    import json as _json
    try:
        with get_cursor() as cursor:
            cursor.execute(
                """INSERT INTO maps
                     (slug, name, subtitle, city_id, vote_type_list_id, custom_vote_types,
                      allow_suggestions, passcode_hash, created_by_ip_hash, symbol, style,
                      network)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (slug, name, subtitle or None, city_id, vote_type_list_id,
                 _json.dumps(custom_vote_types) if custom_vote_types else None,
                 allow_suggestions, passcode_hash, created_by_ip_hash, symbol or None,
                 style or None, network or None),
            )
        return get_map(slug)
    except Exception as e:
        logger.error(f"[DB] Failed to create map '{slug}': {e}")
        return None


def get_map_passcode_hash(slug: str) -> Optional[str]:
    if not DATABASE_URL:
        return None
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT passcode_hash FROM maps WHERE slug = %s", (slug,))
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error(f"[DB] Failed to read passcode hash for '{slug}': {e}")
        return None
