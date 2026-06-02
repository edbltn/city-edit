"""
PostgreSQL database module for persistent vote storage.

Provides persistence layer for votes, complementing Redis real-time state.
Redis handles fast reads/writes and pub/sub; Postgres stores permanent history.
"""
import logging
import os
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

# Database connection string from environment
DATABASE_URL = os.environ.get("DATABASE_URL")

# Connection pool (simple approach - one connection per request)
_connection: Optional[psycopg2.extensions.connection] = None


def get_connection() -> psycopg2.extensions.connection:
    """Get or create database connection."""
    global _connection

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable not set")

    if _connection is None or _connection.closed:
        _connection = psycopg2.connect(DATABASE_URL)
        _connection.autocommit = True

    return _connection


@contextmanager
def get_cursor():
    """Context manager for database cursor."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        yield cursor
    finally:
        cursor.close()


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
            # Create votes table for segment-level vote history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS votes (
                    id SERIAL PRIMARY KEY,
                    segment_key TEXT NOT NULL,
                    mode VARCHAR(10) NOT NULL,
                    ip_hash VARCHAR(16) NOT NULL,
                    weight DECIMAL(10,6) NOT NULL,
                    vote_type TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # Create hex_votes table for aggregated hex votes
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS hex_votes (
                    id SERIAL PRIMARY KEY,
                    hex_id VARCHAR(20) NOT NULL,
                    resolution INT NOT NULL,
                    mode VARCHAR(10) NOT NULL,
                    weight DECIMAL(10,6) NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # Create indexes if they don't exist
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_votes_segment ON votes(segment_key)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_votes_mode ON votes(mode)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_votes_ip ON votes(ip_hash)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_hex_votes_hex ON hex_votes(hex_id, resolution)
            """)

            # Add vote_type column if it doesn't exist (migration)
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'votes' AND column_name = 'vote_type'
                    ) THEN
                        ALTER TABLE votes ADD COLUMN vote_type TEXT;
                    END IF;
                END $$;
            """)

            # Add unique constraint for deduplication (IP + segment + vote_type)
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'votes_unique_ip_segment_type'
                    ) THEN
                        ALTER TABLE votes ADD CONSTRAINT votes_unique_ip_segment_type
                        UNIQUE (segment_key, ip_hash, vote_type);
                    END IF;
                END $$;
            """)

            # Node-level vote history. Mirrors `votes` so node tooltips and the
            # heatmap can show real per-node counts instead of derived max.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS node_votes (
                    id SERIAL PRIMARY KEY,
                    node_key TEXT NOT NULL,
                    mode VARCHAR(10) NOT NULL,
                    ip_hash VARCHAR(16) NOT NULL,
                    weight DECIMAL(10,6) NOT NULL,
                    vote_type TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_node_votes_node ON node_votes(node_key)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_node_votes_mode ON node_votes(mode)
            """)
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'node_votes_unique_ip_node_type'
                    ) THEN
                        ALTER TABLE node_votes ADD CONSTRAINT node_votes_unique_ip_node_type
                        UNIQUE (node_key, ip_hash, vote_type);
                    END IF;
                END $$;
            """)

            # ── New packed-key vote tables ──
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vote_types (
                    id SERIAL PRIMARY KEY,
                    label TEXT UNIQUE NOT NULL
                )
            """)

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
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            _migrate_edge_votes(cursor)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_edge_votes_edge ON edge_votes(edge_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_edge_votes_vt ON edge_votes(vote_type_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_edge_votes_map ON edge_votes(map_slug)")

            # Vote-type lists: named collections (preset or user-created custom).
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vote_type_lists (
                    id SERIAL PRIMARY KEY,
                    key TEXT UNIQUE,
                    name TEXT NOT NULL,
                    is_preset BOOLEAN NOT NULL DEFAULT FALSE,
                    vote_types JSONB NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

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
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_maps_city ON maps(city_id)
            """)

            logger.info("[DB] Database schema initialized successfully")
            return True

    except Exception as e:
        logger.error(f"[DB] Failed to initialize database: {e}")
        return False


def record_segment_votes(segments: list, mode: str, ip_hash: str, vote_type: str = ""):
    """Record segment votes to database for persistence."""
    if not DATABASE_URL:
        return

    try:
        with get_cursor() as cursor:
            data = []
            for seg in segments:
                if len(seg) != 2:
                    continue
                coord1, coord2 = seg
                if coord1 > coord2:
                    coord1, coord2 = coord2, coord1
                segment_key = f"{coord1[0]:.6f},{coord1[1]:.6f}|{coord2[0]:.6f},{coord2[1]:.6f}|{mode}"
                data.append((segment_key, mode, ip_hash, 1.0, vote_type or None))

            if data:
                execute_values(
                    cursor,
                    """INSERT INTO votes (segment_key, mode, ip_hash, weight, vote_type) VALUES %s
                       ON CONFLICT (segment_key, ip_hash, vote_type) DO NOTHING""",
                    data
                )
                logger.info(f"[DB] Recorded {len(data)} segment votes to database")

    except Exception as e:
        logger.error(f"[DB] Failed to record segment votes: {e}")


def record_hex_votes(hex_votes: dict, mode: str, weight: float):
    """
    Record hex votes to database for persistence.

    Args:
        hex_votes: Dict of {hex_id: weight} for each resolution
        mode: Transport mode
        weight: Vote weight
    """
    if not DATABASE_URL:
        return

    try:
        with get_cursor() as cursor:
            data = []
            for hex_id, resolution in hex_votes.items():
                data.append((hex_id, resolution, mode, weight))

            if data:
                execute_values(
                    cursor,
                    "INSERT INTO hex_votes (hex_id, resolution, mode, weight) VALUES %s",
                    data
                )

    except Exception as e:
        logger.error(f"[DB] Failed to record hex votes: {e}")


def get_total_votes_by_mode() -> dict:
    """Get total vote count by mode for analytics."""
    if not DATABASE_URL:
        return {}

    try:
        with get_cursor() as cursor:
            cursor.execute("""
                SELECT mode, COUNT(*) as count, SUM(weight) as total_weight
                FROM votes
                GROUP BY mode
            """)
            return {
                row[0]: {"count": row[1], "weight": float(row[2])}
                for row in cursor.fetchall()
            }
    except Exception as e:
        logger.error(f"[DB] Failed to get vote totals: {e}")
        return {}


def get_unique_voters() -> int:
    """Get count of unique voters (by IP hash)."""
    if not DATABASE_URL:
        return 0

    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT COUNT(DISTINCT ip_hash) FROM votes")
            return cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"[DB] Failed to get unique voters: {e}")
        return 0


def record_node_votes(coords: list, mode: str, ip_hash: str, vote_type: str = ""):
    """Record one row per unique node touched by a route, for persistence."""
    if not DATABASE_URL or not coords:
        return

    try:
        with get_cursor() as cursor:
            data = []
            seen: set[str] = set()
            for coord in coords:
                lon, lat = coord[0], coord[1]
                key = f"{lon:.6f},{lat:.6f}|{mode}"
                if key in seen:
                    continue
                seen.add(key)
                data.append((key, mode, ip_hash, 1.0, vote_type or None))

            if data:
                execute_values(
                    cursor,
                    """INSERT INTO node_votes (node_key, mode, ip_hash, weight, vote_type) VALUES %s
                       ON CONFLICT (node_key, ip_hash, vote_type) DO NOTHING""",
                    data,
                )
                logger.info(f"[DB] Recorded {len(data)} node votes to database")

    except Exception as e:
        logger.error(f"[DB] Failed to record node votes: {e}")


def record_edge_votes(
    map_slug: str, edge_ids: list[int], vt_id: int,
    device_id: str, ip_hash: str | None = None, direction: int = 1,
):
    """Persist edge votes, scoped to a map and deduped per device.

    One row per (map, edge, vote_type, device); on conflict the direction is
    updated so a reversal (up↔down) overwrites the prior vote.
    """
    if not DATABASE_URL or not edge_ids:
        return

    dir_val = 1 if direction >= 0 else -1
    try:
        with get_cursor() as cursor:
            data = [(map_slug, eid, vt_id, device_id, ip_hash, dir_val) for eid in edge_ids]
            execute_values(
                cursor,
                """INSERT INTO edge_votes
                   (map_slug, edge_id, vote_type_id, device_id, ip_hash, direction)
                   VALUES %s
                   ON CONFLICT (map_slug, edge_id, vote_type_id, device_id)
                   DO UPDATE SET direction = EXCLUDED.direction, ip_hash = EXCLUDED.ip_hash""",
                data,
            )
            logger.info(f"[DB] Recorded {len(data)} edge votes for '{map_slug}' (dir={dir_val})")
    except Exception as e:
        logger.error(f"[DB] Failed to record edge votes: {e}")


def get_voter_edge_directions(
    map_slug: str, edge_ids: list[int], device_id: str
) -> dict[int, dict[int, int]]:
    """Return {edge_id: {vote_type_id: direction}} for one device on a map's edges."""
    if not DATABASE_URL or not edge_ids:
        return {}
    try:
        with get_cursor() as cursor:
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


def get_voter_edge_direction(
    map_slug: str, edge_id: int, vt_id: int, device_id: str
) -> int:
    """Return a device's current direction for one proposal (+1/-1), or 0 if none."""
    if not DATABASE_URL:
        return 0
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT direction FROM edge_votes "
                "WHERE map_slug = %s AND edge_id = %s AND vote_type_id = %s AND device_id = %s",
                (map_slug, edge_id, vt_id, device_id),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0
    except Exception as e:
        logger.error(f"[DB] Failed to read voter edge direction: {e}")
        return 0


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


def get_or_create_vote_type_id(label: str) -> int:
    """Return the id for a vote-type label, creating the row if needed."""
    if not DATABASE_URL or not label:
        return 0
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO vote_types (label) VALUES (%s) "
                "ON CONFLICT (label) DO UPDATE SET label = EXCLUDED.label RETURNING id",
                (label,),
            )
            return cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"[DB] Failed to get/create vote type '{label}': {e}")
        return 0


# ── Aggregates / admin (replaces inline SQL elsewhere) ───────────────────────

def aggregate_votes_for_replay() -> list[tuple[str, int, int, int, int]]:
    """Per (map_slug, edge_id, vote_type_id, direction) vote counts, for Redis replay."""
    if not DATABASE_URL:
        return []
    try:
        with get_cursor() as cursor:
            cursor.execute("""
                SELECT map_slug, edge_id, vote_type_id, direction, COUNT(*)
                FROM edge_votes
                GROUP BY map_slug, edge_id, vote_type_id, direction
            """)
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"[DB] Failed to aggregate votes: {e}")
        return []


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
                    """INSERT INTO vote_type_lists (key, name, is_preset, vote_types)
                       VALUES (%s, %s, TRUE, %s)
                       ON CONFLICT (key) DO UPDATE
                         SET name = EXCLUDED.name, vote_types = EXCLUDED.vote_types
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
        logger.info("[DB] Seeded preset vote-type lists and maps")
    except Exception as e:
        logger.error(f"[DB] Failed to seed presets: {e}")


def list_vote_type_lists() -> list[dict]:
    """Return preset (and named) vote-type lists for the propose form."""
    if not DATABASE_URL:
        return []
    try:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT id, key, name, is_preset, vote_types "
                "FROM vote_type_lists WHERE is_preset = TRUE ORDER BY id"
            )
            return [
                {"id": r[0], "key": r[1], "name": r[2], "isPreset": r[3], "voteTypes": r[4]}
                for r in cursor.fetchall()
            ]
    except Exception as e:
        logger.error(f"[DB] Failed to list vote-type lists: {e}")
        return []


# Preset subdomains double as the packed-key "mode" string and the client style
# key. User maps (no subdomain) share the neutral "walk" mode + "default" style;
# each map's votes are isolated by slug, so a shared mode is safe.
_PRESET_STYLES = {"bikepaths", "trees", "walkways"}


def _map_row_to_dict(row) -> dict:
    """Shape a maps JOIN vote_type_lists row into the public map dict."""
    (slug, name, subtitle, city_id, allow_suggestions, has_passcode,
     subdomain, list_vote_types, custom_vote_types, vote_count) = row
    return {
        "slug": slug,
        "name": name,
        "subtitle": subtitle or "",
        "cityId": city_id,
        "allowSuggestions": allow_suggestions,
        "requiresPasscode": has_passcode,
        "subdomain": subdomain,
        "mode": subdomain if subdomain in _PRESET_STYLES else "walk",
        "style": subdomain if subdomain in _PRESET_STYLES else "default",
        "voteCount": int(vote_count or 0),
        "voteTypes": custom_vote_types or list_vote_types or [],
    }


_MAP_SELECT = """
    SELECT m.slug, m.name, m.subtitle, m.city_id, m.allow_suggestions,
           (m.passcode_hash IS NOT NULL) AS has_passcode,
           m.subdomain, vtl.vote_types, m.custom_vote_types,
           COALESCE(vc.cnt, 0) AS vote_count
    FROM maps m
    LEFT JOIN vote_type_lists vtl ON vtl.id = m.vote_type_list_id
    LEFT JOIN (SELECT map_slug, COUNT(*) AS cnt FROM edge_votes GROUP BY map_slug) vc
      ON vc.map_slug = m.slug
"""


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


def get_map(slug: str) -> Optional[dict]:
    if not DATABASE_URL:
        return None
    try:
        with get_cursor() as cursor:
            cursor.execute(_MAP_SELECT + " WHERE m.slug = %s", (slug,))
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
            cursor.execute(_MAP_SELECT + " WHERE m.subdomain = %s LIMIT 1", (subdomain,))
            row = cursor.fetchone()
            return _map_row_to_dict(row) if row else None
    except Exception as e:
        logger.error(f"[DB] Failed to get map by subdomain '{subdomain}': {e}")
        return None


def slug_available(slug: str) -> bool:
    if not DATABASE_URL:
        return False
    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM maps WHERE slug = %s", (slug,))
            return cursor.fetchone() is None
    except Exception as e:
        logger.error(f"[DB] slug_available check failed: {e}")
        return False


def create_map(
    slug: str, name: str, city_id: str,
    subtitle: Optional[str] = None,
    vote_type_list_id: Optional[int] = None,
    custom_vote_types: Optional[list] = None,
    allow_suggestions: bool = True,
    passcode_hash: Optional[str] = None,
    created_by_ip_hash: Optional[str] = None,
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
                      allow_suggestions, passcode_hash, created_by_ip_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (slug, name, subtitle or None, city_id, vote_type_list_id,
                 _json.dumps(custom_vote_types) if custom_vote_types else None,
                 allow_suggestions, passcode_hash, created_by_ip_hash),
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


def record_point_vote(point: list, mode: str, ip_hash: str, vote_type: str = ""):
    """
    Record a point vote to database for persistence.

    Args:
        point: [lat, lon] coordinates
        mode: Transport mode (bike, walk, drive)
        ip_hash: Hashed client IP
        vote_type: Natural language description of the vote
    """
    if not DATABASE_URL:
        return

    try:
        with get_cursor() as cursor:
            # Use a simple key format for point votes (no end point)
            lat, lon = point[0], point[1]
            segment_key = f"{lon:.6f},{lat:.6f}||{mode}"  # Empty end point

            cursor.execute(
                """INSERT INTO votes (segment_key, mode, ip_hash, weight, vote_type) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (segment_key, ip_hash, vote_type) DO NOTHING""",
                (segment_key, mode, ip_hash, 1.0, vote_type or None)
            )
            logger.info(f"[DB] Recorded point vote to database")

    except Exception as e:
        logger.error(f"[DB] Failed to record point vote: {e}")
