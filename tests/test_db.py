"""Tests for SQLite database layer."""

from datetime import UTC, datetime

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent


class TestDatabase:
    async def test_initialize_creates_tables(self, db: Database):
        tables = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = [row["name"] for row in tables]
        assert "events" in table_names
        assert "event_state_changes" in table_names

    async def test_initialize_creates_indexes(self, db: Database):
        indexes = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
        index_names = [row["name"] for row in indexes]
        assert "idx_events_status" in index_names
        assert "idx_events_kickoff" in index_names


class TestEventRepository:
    @pytest.fixture
    def repo(self, db: Database) -> EventRepository:
        return EventRepository(db)

    def _make_event(self, **overrides) -> TrackedEvent:
        defaults = dict(
            betpawa_event_id="99001",
            home_team="Arsenal",
            away_team="Chelsea",
            competition="England Premier League",
            country="England",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        defaults.update(overrides)
        return TrackedEvent(**defaults)

    async def test_insert_and_get_by_betpawa_id(self, repo: EventRepository):
        event = self._make_event()
        row_id = await repo.insert_event(event)
        assert row_id > 0

        fetched = await repo.get_by_betpawa_id("99001")
        assert fetched is not None
        assert fetched.id == row_id
        assert fetched.home_team == "Arsenal"
        assert fetched.status == EventStatus.PREMATCH
        assert len(fetched.provider_ids) == 1
        assert fetched.provider_ids[0].type == ProviderType.SPORTRADAR

    async def test_get_by_betpawa_id_not_found(self, repo: EventRepository):
        result = await repo.get_by_betpawa_id("nonexistent")
        assert result is None

    async def test_update_status(self, repo: EventRepository):
        event = self._make_event()
        row_id = await repo.insert_event(event)

        now = datetime(2026, 4, 15, 15, 5, tzinfo=UTC)
        await repo.update_status(row_id, EventStatus.LATE, updated_at=now)

        fetched = await repo.get_by_betpawa_id("99001")
        assert fetched is not None
        assert fetched.status == EventStatus.LATE

    async def test_update_live_fields(self, repo: EventRepository):
        event = self._make_event()
        row_id = await repo.insert_event(event)

        live_time = datetime(2026, 4, 15, 15, 7, tzinfo=UTC)
        await repo.update_live_fields(
            row_id, first_seen_live=live_time, transition_delay_sec=420
        )

        fetched = await repo.get_by_betpawa_id("99001")
        assert fetched is not None
        assert fetched.first_seen_live == live_time
        assert fetched.transition_delay_sec == 420

    async def test_update_slack_ts(self, repo: EventRepository):
        event = self._make_event()
        row_id = await repo.insert_event(event)

        await repo.update_slack_ts(row_id, "1234567890.123456")

        fetched = await repo.get_by_betpawa_id("99001")
        assert fetched is not None
        assert fetched.slack_message_ts == "1234567890.123456"

    async def test_get_active_events(self, repo: EventRepository):
        await repo.insert_event(self._make_event(betpawa_event_id="1"))
        e2 = self._make_event(betpawa_event_id="2", status=EventStatus.LATE)
        await repo.insert_event(e2)
        e3 = self._make_event(betpawa_event_id="3", status=EventStatus.LIVE)
        await repo.insert_event(e3)

        active = await repo.get_active_events()
        active_ids = {e.betpawa_event_id for e in active}
        assert active_ids == {"1", "2"}

    async def test_insert_state_change(self, repo: EventRepository):
        event = self._make_event()
        row_id = await repo.insert_event(event)

        now = datetime(2026, 4, 15, 15, 5, tzinfo=UTC)
        await repo.insert_state_change(
            event_id=row_id,
            old_status=EventStatus.PREMATCH,
            new_status=EventStatus.LATE,
            changed_at=now,
            details="5min past kickoff",
        )

        changes = await repo.get_state_changes(row_id)
        assert len(changes) == 1
        assert changes[0].old_status == EventStatus.PREMATCH
        assert changes[0].new_status == EventStatus.LATE
        assert changes[0].details == "5min past kickoff"

    async def test_get_events_in_date_range(self, repo: EventRepository):
        await repo.insert_event(self._make_event(
            betpawa_event_id="1",
            scheduled_kickoff=datetime(2026, 4, 14, 15, 0, tzinfo=UTC),
        ))
        await repo.insert_event(self._make_event(
            betpawa_event_id="2",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
        ))
        await repo.insert_event(self._make_event(
            betpawa_event_id="3",
            scheduled_kickoff=datetime(2026, 4, 16, 15, 0, tzinfo=UTC),
        ))

        start = datetime(2026, 4, 14, 0, 0, tzinfo=UTC)
        end = datetime(2026, 4, 15, 23, 59, tzinfo=UTC)
        events = await repo.get_events_in_date_range(start, end)
        ids = {e.betpawa_event_id for e in events}
        assert ids == {"1", "2"}

    async def test_delete_old_events(self, repo: EventRepository):
        await repo.insert_event(self._make_event(
            betpawa_event_id="old",
            scheduled_kickoff=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
            status=EventStatus.LIVE,
        ))
        await repo.insert_event(self._make_event(
            betpawa_event_id="recent",
            scheduled_kickoff=datetime(2026, 4, 14, 15, 0, tzinfo=UTC),
        ))

        cutoff = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
        deleted = await repo.delete_events_before(cutoff)
        assert deleted == 1

        remaining = await repo.get_by_betpawa_id("recent")
        assert remaining is not None
        gone = await repo.get_by_betpawa_id("old")
        assert gone is None
