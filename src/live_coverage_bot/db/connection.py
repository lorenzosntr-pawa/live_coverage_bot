"""SQLite database connection manager."""

import logging
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    betpawa_event_id TEXT UNIQUE NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    competition TEXT NOT NULL,
    country TEXT,
    scheduled_kickoff TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_ids TEXT NOT NULL,
    first_seen_prematch TEXT NOT NULL,
    first_seen_live TEXT,
    transition_delay_sec INTEGER,
    slack_message_ts TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_state_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    old_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    details TEXT
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    phase TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    markets_json TEXT NOT NULL,
    total_market_count INTEGER NOT NULL,
    total_selection_count INTEGER NOT NULL,
    suspended_count INTEGER NOT NULL,
    fetch_duration_ms INTEGER,
    UNIQUE (event_id, phase)
);

CREATE TABLE IF NOT EXISTS market_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    compared_at TEXT NOT NULL,
    prematch_phase TEXT NOT NULL,
    markets_added INTEGER NOT NULL,
    markets_dropped INTEGER NOT NULL,
    markets_kept INTEGER NOT NULL,
    retention_pct REAL NOT NULL,
    dropped_key_markets TEXT NOT NULL,
    max_odds_shift_pct REAL NOT NULL,
    triggered_alert INTEGER NOT NULL,
    details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_heartbeat TEXT NOT NULL,
    started_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE INDEX IF NOT EXISTS idx_events_kickoff ON events(scheduled_kickoff);
CREATE INDEX IF NOT EXISTS idx_snapshots_event ON market_snapshots(event_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_taken_at ON market_snapshots(taken_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_phase ON market_snapshots(phase);
CREATE INDEX IF NOT EXISTS idx_comparisons_event ON market_comparisons(event_id);
CREATE INDEX IF NOT EXISTS idx_comparisons_compared_at ON market_comparisons(compared_at);
CREATE INDEX IF NOT EXISTS idx_comparisons_alert ON market_comparisons(triggered_alert);
"""

MIGRATIONS_SQL = """
-- Feature 1: Enhanced REMOVED tracking
ALTER TABLE events ADD COLUMN removed_at TEXT;
ALTER TABLE events ADD COLUMN pre_removal_market_count INTEGER;

-- Feature 2: Match state on snapshots
ALTER TABLE market_snapshots ADD COLUMN match_state_json TEXT;

-- Feature 3: Snapshot phase on comparisons
ALTER TABLE market_comparisons ADD COLUMN snapshot_phase TEXT;

-- Feature 4: Competition ID for league filtering
ALTER TABLE events ADD COLUMN competition_id TEXT DEFAULT '';
"""


class Database:
    """Async SQLite database manager."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open connection and create tables."""
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.commit()
        # Run additive migrations — ALTER TABLE fails gracefully if column exists
        for statement in MIGRATIONS_SQL.strip().split(";"):
            # Strip comment lines and surrounding whitespace
            lines = [ln for ln in statement.splitlines() if not ln.strip().startswith("--")]
            stmt = "\n".join(lines).strip()
            if not stmt:
                continue
            try:
                await self._conn.execute(stmt)
            except Exception:
                pass  # Column already exists
        await self._conn.commit()
        logger.info("Database initialized at %s", self._path)

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Cursor:
        """Execute a single SQL statement."""
        assert self._conn is not None
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cursor

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        """Fetch a single row as a dict."""
        assert self._conn is not None
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Fetch all rows as a list of dicts."""
        assert self._conn is not None
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
