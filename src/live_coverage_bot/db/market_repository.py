"""Market snapshot and comparison persistence."""

import json
from datetime import datetime

from live_coverage_bot.db.connection import Database
from live_coverage_bot.models.markets import (
    JSON_SEPARATORS,
    ComparisonDetails,
    MarketComparison,
    MarketSnapshot,
    MatchState,
    SnapshotPhase,
)


# Prematch phases ordered closest-to-kickoff first for latest-lookup.
PREMATCH_PHASES_CLOSEST_FIRST = (
    SnapshotPhase.PREMATCH_1,
    SnapshotPhase.PRE_REMOVAL,
    SnapshotPhase.PREMATCH_15,
    SnapshotPhase.PREMATCH_60,
)

LIVE_PHASE_PREFERENCE = (
    SnapshotPhase.LIVE_5,
    SnapshotPhase.LIVE_2,
    SnapshotPhase.LIVE_0,
    SnapshotPhase.LIVE,  # Legacy
)


class MarketRepository:
    """Persistence for market snapshots and comparisons."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_snapshot(
        self, snapshot: MarketSnapshot, fetch_duration_ms: int | None = None
    ) -> int:
        match_state_json = (
            json.dumps(snapshot.match_state.model_dump(), separators=JSON_SEPARATORS)
            if snapshot.match_state
            else None
        )
        cursor = await self._db.execute(
            """INSERT INTO market_snapshots
            (event_id, phase, taken_at, markets_json, total_market_count,
             total_selection_count, suspended_count, fetch_duration_ms, match_state_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot.event_id,
                snapshot.phase.value,
                snapshot.taken_at.isoformat(),
                snapshot.markets_json,
                snapshot.total_market_count,
                snapshot.total_selection_count,
                snapshot.suspended_count,
                fetch_duration_ms,
                match_state_json,
            ),
        )
        return cursor.lastrowid

    async def phases_taken_for_event(self, event_id: int) -> set[SnapshotPhase]:
        rows = await self._db.fetch_all(
            "SELECT phase FROM market_snapshots WHERE event_id = ?",
            (event_id,),
        )
        return {SnapshotPhase(r["phase"]) for r in rows}

    async def get_latest_prematch_snapshot(
        self, event_id: int
    ) -> MarketSnapshot | None:
        """Return the prematch snapshot closest to kickoff (PREMATCH_1 preferred)."""
        for phase in PREMATCH_PHASES_CLOSEST_FIRST:
            row = await self._db.fetch_one(
                "SELECT * FROM market_snapshots WHERE event_id = ? AND phase = ?",
                (event_id, phase.value),
            )
            if row is not None:
                return self._row_to_snapshot(row)
        return None

    async def get_snapshots_for_event(self, event_id: int) -> list[MarketSnapshot]:
        rows = await self._db.fetch_all(
            "SELECT * FROM market_snapshots WHERE event_id = ? ORDER BY taken_at",
            (event_id,),
        )
        return [self._row_to_snapshot(r) for r in rows]

    async def insert_comparison(self, cmp: MarketComparison) -> int:
        cursor = await self._db.execute(
            """INSERT INTO market_comparisons
            (event_id, compared_at, prematch_phase, markets_added, markets_dropped,
             markets_kept, retention_pct, dropped_key_markets, max_odds_shift_pct,
             triggered_alert, details_json, snapshot_phase)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cmp.event_id,
                cmp.compared_at.isoformat(),
                cmp.prematch_phase.value,
                cmp.markets_added,
                cmp.markets_dropped,
                cmp.markets_kept,
                cmp.retention_pct,
                json.dumps(cmp.dropped_key_markets),
                cmp.max_odds_shift_pct,
                1 if cmp.triggered_alert else 0,
                json.dumps(cmp.details.model_dump(), separators=JSON_SEPARATORS),
                cmp.snapshot_phase.value if cmp.snapshot_phase else None,
            ),
        )
        return cursor.lastrowid

    async def get_comparisons_for_event(self, event_id: int) -> list[MarketComparison]:
        """Fetch all comparisons for an event, ordered by compared_at."""
        rows = await self._db.fetch_all(
            "SELECT * FROM market_comparisons WHERE event_id = ? ORDER BY compared_at",
            (event_id,),
        )
        return [self._row_to_comparison(r) for r in rows]

    async def get_comparison_for_event(self, event_id: int) -> MarketComparison | None:
        row = await self._db.fetch_one(
            "SELECT * FROM market_comparisons WHERE event_id = ?",
            (event_id,),
        )
        if row is None:
            return None
        return self._row_to_comparison(row)

    async def get_best_comparison_for_event(self, event_id: int) -> "MarketComparison | None":
        """Return the best (latest live phase) comparison for weekly reporting."""
        for phase in LIVE_PHASE_PREFERENCE:
            row = await self._db.fetch_one(
                "SELECT * FROM market_comparisons WHERE event_id = ? AND snapshot_phase = ?",
                (event_id, phase.value),
            )
            if row is not None:
                return self._row_to_comparison(row)
        # Fallback: any comparison for this event (backward compat)
        return await self.get_comparison_for_event(event_id)

    async def get_comparisons_in_date_range(
        self, start: datetime, end: datetime
    ) -> list[MarketComparison]:
        rows = await self._db.fetch_all(
            "SELECT * FROM market_comparisons WHERE compared_at >= ? AND compared_at <= ?",
            (start.isoformat(), end.isoformat()),
        )
        return [self._row_to_comparison(r) for r in rows]

    def _row_to_snapshot(self, row: dict) -> MarketSnapshot:
        match_state = None
        match_state_raw = row.get("match_state_json")
        if match_state_raw:
            match_state = MatchState(**json.loads(match_state_raw))
        return MarketSnapshot(
            id=row["id"],
            event_id=row["event_id"],
            phase=SnapshotPhase(row["phase"]),
            taken_at=datetime.fromisoformat(row["taken_at"]),
            markets=MarketSnapshot.markets_from_json(row["markets_json"]),
            total_market_count=row["total_market_count"],
            total_selection_count=row["total_selection_count"],
            suspended_count=row["suspended_count"],
            match_state=match_state,
        )

    def _row_to_comparison(self, row: dict) -> MarketComparison:
        return MarketComparison(
            id=row["id"],
            event_id=row["event_id"],
            compared_at=datetime.fromisoformat(row["compared_at"]),
            prematch_phase=SnapshotPhase(row["prematch_phase"]),
            snapshot_phase=SnapshotPhase(row["snapshot_phase"]) if row.get("snapshot_phase") else None,
            markets_added=row["markets_added"],
            markets_dropped=row["markets_dropped"],
            markets_kept=row["markets_kept"],
            retention_pct=row["retention_pct"],
            dropped_key_markets=json.loads(row["dropped_key_markets"]),
            max_odds_shift_pct=row["max_odds_shift_pct"],
            triggered_alert=bool(row["triggered_alert"]),
            details=ComparisonDetails(**json.loads(row["details_json"])),
        )
