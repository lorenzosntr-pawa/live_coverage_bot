"""Event CRUD operations against SQLite."""

from datetime import datetime

from live_coverage_bot.db.connection import Database
from live_coverage_bot.models.events import EventStatus, StateChange, TrackedEvent


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO datetime string, returning None if the value is None."""
    return datetime.fromisoformat(value) if value else None


class EventRepository:
    """Repository for tracked event persistence."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_event(self, event: TrackedEvent) -> int:
        """Insert a new tracked event. Returns the row ID."""
        cursor = await self._db.execute(
            """INSERT INTO events
            (betpawa_event_id, home_team, away_team, competition, country,
             scheduled_kickoff, status, provider_ids, first_seen_prematch,
             first_seen_live, transition_delay_sec, slack_message_ts,
             removed_at, pre_removal_market_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.betpawa_event_id,
                event.home_team,
                event.away_team,
                event.competition,
                event.country,
                event.scheduled_kickoff.isoformat(),
                event.status.value,
                event.provider_ids_json,
                event.first_seen_prematch.isoformat(),
                event.first_seen_live.isoformat() if event.first_seen_live else None,
                event.transition_delay_sec,
                event.slack_message_ts,
                event.removed_at.isoformat() if event.removed_at else None,
                event.pre_removal_market_count,
            ),
        )
        return cursor.lastrowid

    async def get_by_betpawa_id(self, betpawa_event_id: str) -> TrackedEvent | None:
        """Fetch event by BetPawa event ID."""
        row = await self._db.fetch_one(
            "SELECT * FROM events WHERE betpawa_event_id = ?",
            (betpawa_event_id,),
        )
        if row is None:
            return None
        return self._row_to_event(row)

    async def get_by_id(self, event_id: int) -> "TrackedEvent | None":
        """Fetch event by internal DB ID."""
        row = await self._db.fetch_one(
            "SELECT * FROM events WHERE id = ?",
            (event_id,),
        )
        if row is None:
            return None
        return self._row_to_event(row)

    async def get_active_events(self) -> list[TrackedEvent]:
        """Fetch all events in non-terminal states (PREMATCH, LATE)."""
        rows = await self._db.fetch_all(
            "SELECT * FROM events WHERE status IN (?, ?)",
            (EventStatus.PREMATCH.value, EventStatus.LATE.value),
        )
        return [self._row_to_event(row) for row in rows]

    async def get_recent_removed_events(self, since: datetime) -> list[TrackedEvent]:
        """Fetch REMOVED events with kickoff on or after `since`, for possible recovery."""
        rows = await self._db.fetch_all(
            "SELECT * FROM events WHERE status = ? AND scheduled_kickoff >= ?",
            (EventStatus.REMOVED.value, since.isoformat()),
        )
        return [self._row_to_event(row) for row in rows]

    async def update_status(
        self, event_id: int, status: EventStatus, updated_at: datetime
    ) -> None:
        """Update event status."""
        await self._db.execute(
            "UPDATE events SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, updated_at.isoformat(), event_id),
        )

    async def update_live_fields(
        self,
        event_id: int,
        first_seen_live: datetime,
        transition_delay_sec: int,
    ) -> None:
        """Update live-specific fields when event goes live."""
        await self._db.execute(
            """UPDATE events SET first_seen_live = ?, transition_delay_sec = ?,
            updated_at = ? WHERE id = ?""",
            (
                first_seen_live.isoformat(),
                transition_delay_sec,
                first_seen_live.isoformat(),
                event_id,
            ),
        )

    async def update_removed_fields(
        self, event_id: int, removed_at: datetime, market_count: int | None = None
    ) -> None:
        """Update removal-specific fields."""
        await self._db.execute(
            "UPDATE events SET removed_at = ?, pre_removal_market_count = ?, updated_at = ? WHERE id = ?",
            (removed_at.isoformat(), market_count, removed_at.isoformat(), event_id),
        )

    async def update_slack_ts(self, event_id: int, slack_ts: str) -> None:
        """Store Slack message timestamp for an event."""
        await self._db.execute(
            "UPDATE events SET slack_message_ts = ? WHERE id = ?",
            (slack_ts, event_id),
        )

    async def insert_state_change(
        self,
        event_id: int,
        old_status: EventStatus,
        new_status: EventStatus,
        changed_at: datetime,
        details: str | None = None,
    ) -> None:
        """Record an event state transition."""
        await self._db.execute(
            """INSERT INTO event_state_changes
            (event_id, old_status, new_status, changed_at, details)
            VALUES (?, ?, ?, ?, ?)""",
            (
                event_id,
                old_status.value,
                new_status.value,
                changed_at.isoformat(),
                details,
            ),
        )

    async def get_state_changes(self, event_id: int) -> list[StateChange]:
        """Fetch all state changes for an event, ordered chronologically."""
        rows = await self._db.fetch_all(
            "SELECT * FROM event_state_changes WHERE event_id = ? ORDER BY changed_at",
            (event_id,),
        )
        return [
            StateChange(
                id=row["id"],
                event_id=row["event_id"],
                old_status=EventStatus(row["old_status"]),
                new_status=EventStatus(row["new_status"]),
                changed_at=datetime.fromisoformat(row["changed_at"]),
                details=row["details"],
            )
            for row in rows
        ]

    async def get_events_in_date_range(
        self, start: datetime, end: datetime
    ) -> list[TrackedEvent]:
        """Fetch events with kickoff in the given range."""
        rows = await self._db.fetch_all(
            "SELECT * FROM events WHERE scheduled_kickoff >= ? AND scheduled_kickoff <= ?",
            (start.isoformat(), end.isoformat()),
        )
        return [self._row_to_event(row) for row in rows]

    async def delete_events_before(self, cutoff: datetime) -> int:
        """Delete events with kickoff before the cutoff. Returns count deleted."""
        cursor = await self._db.execute(
            "DELETE FROM events WHERE scheduled_kickoff < ?",
            (cutoff.isoformat(),),
        )
        return cursor.rowcount

    async def get_last_heartbeat(self) -> datetime | None:
        """Read the last heartbeat timestamp. Returns None on first run."""
        row = await self._db.fetch_one(
            "SELECT last_heartbeat FROM bot_state WHERE id = 1"
        )
        if row is None:
            return None
        return datetime.fromisoformat(row["last_heartbeat"])

    async def upsert_heartbeat(
        self, now: datetime, started_at: datetime | None = None
    ) -> None:
        """Insert or update the heartbeat timestamp.

        On first call after startup, pass started_at to record session start.
        Subsequent calls within the same session only update last_heartbeat.
        """
        if started_at is not None:
            await self._db.execute(
                """INSERT INTO bot_state (id, last_heartbeat, started_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_heartbeat = excluded.last_heartbeat,
                    started_at = excluded.started_at""",
                (now.isoformat(), started_at.isoformat()),
            )
        else:
            await self._db.execute(
                "UPDATE bot_state SET last_heartbeat = ? WHERE id = 1",
                (now.isoformat(),),
            )

    def _row_to_event(self, row: dict) -> TrackedEvent:
        """Convert a database row to a TrackedEvent."""
        return TrackedEvent(
            id=row["id"],
            betpawa_event_id=row["betpawa_event_id"],
            home_team=row["home_team"],
            away_team=row["away_team"],
            competition=row["competition"],
            country=row["country"],
            scheduled_kickoff=datetime.fromisoformat(row["scheduled_kickoff"]),
            status=EventStatus(row["status"]),
            provider_ids=TrackedEvent.provider_ids_from_json(row["provider_ids"]),
            first_seen_prematch=datetime.fromisoformat(row["first_seen_prematch"]),
            first_seen_live=_parse_dt(row["first_seen_live"]),
            transition_delay_sec=row["transition_delay_sec"],
            slack_message_ts=row["slack_message_ts"],
            removed_at=_parse_dt(row.get("removed_at")),
            pre_removal_market_count=row.get("pre_removal_market_count"),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )
