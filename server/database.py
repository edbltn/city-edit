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

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS edge_votes (
                    id SERIAL PRIMARY KEY,
                    packed_key BIGINT NOT NULL,
                    edge_id INT NOT NULL,
                    mode SMALLINT NOT NULL,
                    vote_type_id INT NOT NULL DEFAULT 0,
                    ip_hash VARCHAR(16) NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE (packed_key, ip_hash)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_edge_votes_edge ON edge_votes(edge_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_edge_votes_mode ON edge_votes(mode)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_edge_votes_vt ON edge_votes(vote_type_id)
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
    edge_ids: list[int], mode_int: int, vt_id: int, ip_hash: str
):
    """Persist packed-key edge votes to Postgres."""
    if not DATABASE_URL or not edge_ids:
        return

    from vote_store import pack

    try:
        with get_cursor() as cursor:
            data = []
            for eid in edge_ids:
                pk = pack(eid, mode_int, vt_id)
                data.append((pk, eid, mode_int, vt_id, ip_hash))
            if data:
                execute_values(
                    cursor,
                    """INSERT INTO edge_votes
                       (packed_key, edge_id, mode, vote_type_id, ip_hash)
                       VALUES %s
                       ON CONFLICT (packed_key, ip_hash) DO NOTHING""",
                    data,
                )
                logger.info(f"[DB] Recorded {len(data)} edge votes")
    except Exception as e:
        logger.error(f"[DB] Failed to record edge votes: {e}")


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
