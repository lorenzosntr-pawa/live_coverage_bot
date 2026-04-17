"""Tests for event lifecycle tracker."""

from datetime import UTC, datetime, timedelta

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.core.tracker import EventLifecycleTracker
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, RemovedResult, TransitionResult


@pytest.fixture
def repo(db: Database) -> EventRepository:
    return EventRepository(db)


@pytest.fixture
def tracker(repo: EventRepository) -> EventLifecycleTracker:
    return EventLifecycleTracker(repo, grace_period_minutes=5, hard_timeout_minutes=90)


def _make_provider_ids():
    return [ProviderID(type=ProviderType.SPORTRADAR, id="12345")]


class TestRegisterPrematchEvents:
    async def test_registers_new_prematch_event(self, tracker, repo):
        now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        new_ids = await tracker.register_prematch_events(
            prematch_feed=[
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "England Premier League",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now,
        )
        assert new_ids == ["99001"]
        event = await repo.get_by_betpawa_id("99001")
        assert event is not None
        assert event.status == EventStatus.PREMATCH

    async def test_skips_already_registered_event(self, tracker, repo):
        now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        feed = [
            {
                "betpawa_event_id": "99001",
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "competition": "England Premier League",
                "country": "England",
                "scheduled_kickoff": kickoff,
                "provider_ids": _make_provider_ids(),
            }
        ]
        await tracker.register_prematch_events(feed, now=now)
        new_ids = await tracker.register_prematch_events(feed, now=now)
        assert new_ids == []


class TestCheckTransitions:
    async def test_prematch_to_live_on_time(self, tracker, repo):
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now_register = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_register,
        )

        now_live = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)
        live_betpawa_ids = {"99001"}
        transitions = await tracker.check_transitions(live_betpawa_ids, now=now_live)

        assert len(transitions) == 1
        t = transitions[0]
        assert t.event.betpawa_event_id == "99001"
        assert t.old_status == EventStatus.PREMATCH
        assert t.new_status == EventStatus.LIVE

        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.LIVE
        assert event.transition_delay_sec == 60

    async def test_prematch_to_late(self, tracker, repo):
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now_register = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_register,
        )

        now_late = kickoff + timedelta(minutes=6)
        transitions = await tracker.check_transitions(set(), now=now_late)

        assert len(transitions) == 1
        assert transitions[0].new_status == EventStatus.LATE

    async def test_late_to_live(self, tracker, repo):
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now_reg = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_reg,
        )

        now_late = kickoff + timedelta(minutes=6)
        await tracker.check_transitions(set(), now=now_late)

        now_live = kickoff + timedelta(minutes=12)
        live_betpawa_ids = {"99001"}
        transitions = await tracker.check_transitions(live_betpawa_ids, now=now_live)

        assert len(transitions) == 1
        assert transitions[0].old_status == EventStatus.LATE
        assert transitions[0].new_status == EventStatus.LIVE

        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.LIVE
        assert event.transition_delay_sec == 720

    async def test_late_to_never_live(self, tracker, repo):
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now_reg = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_reg,
        )

        now_late = kickoff + timedelta(minutes=6)
        await tracker.check_transitions(set(), now=now_late)

        now_timeout = kickoff + timedelta(minutes=91)
        transitions = await tracker.check_transitions(set(), now=now_timeout)

        assert len(transitions) == 1
        assert transitions[0].new_status == EventStatus.NEVER_LIVE


class TestDetectRemoved:
    async def test_prematch_removed(self, tracker, repo):
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now,
        )

        now_check = datetime(2026, 4, 15, 14, 0, tzinfo=UTC)
        removed = await tracker.detect_removed(
            current_prematch_ids=set(), now=now_check
        )
        assert len(removed) == 1
        assert removed[0].event.betpawa_event_id == "99001"

    async def test_not_removed_if_still_in_feed(self, tracker, repo):
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now,
        )

        now_check = datetime(2026, 4, 15, 14, 0, tzinfo=UTC)
        removed = await tracker.detect_removed(
            current_prematch_ids={"99001"}, now=now_check
        )
        assert len(removed) == 0


class TestEnhancedRemoval:
    async def test_detect_removed_records_removed_at(self, db):
        from live_coverage_bot.db.repository import EventRepository
        from live_coverage_bot.core.tracker import EventLifecycleTracker
        from live_coverage_bot.models.events import EventStatus, TrackedEvent
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo)
        event = TrackedEvent(
            betpawa_event_id="400",
            home_team="E", away_team="F", competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 18, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="3")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        await repo.insert_event(event)

        now = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        results = await tracker.detect_removed(set(), now=now)
        assert len(results) == 1

        fetched = await repo.get_by_betpawa_id("400")
        assert fetched is not None
        assert fetched.removed_at == now

    async def test_removed_to_prematch_recovery(self, db):
        from live_coverage_bot.db.repository import EventRepository
        from live_coverage_bot.core.tracker import EventLifecycleTracker
        from live_coverage_bot.models.events import EventStatus, TrackedEvent, TransitionResult
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo)
        event = TrackedEvent(
            betpawa_event_id="401",
            home_team="G", away_team="H", competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 18, 0, tzinfo=UTC),
            status=EventStatus.REMOVED,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="4")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            removed_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
        )
        await repo.insert_event(event)

        now = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        recoveries = await tracker.detect_prematch_recovery({"401"}, now=now)
        assert len(recoveries) == 1
        assert isinstance(recoveries[0], TransitionResult)
        assert recoveries[0].new_status == EventStatus.PREMATCH

        fetched = await repo.get_by_betpawa_id("401")
        assert fetched is not None
        assert fetched.status == EventStatus.PREMATCH


class TestTypedReturns:
    async def test_check_transitions_returns_transition_results(self, db):
        from live_coverage_bot.db.repository import EventRepository
        from live_coverage_bot.core.tracker import EventLifecycleTracker
        from live_coverage_bot.models.events import EventStatus, TrackedEvent
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo)
        event = TrackedEvent(
            betpawa_event_id="100",
            home_team="A",
            away_team="B",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        await repo.insert_event(event)

        now = datetime(2026, 4, 15, 15, 6, tzinfo=UTC)
        results = await tracker.check_transitions(set(), now=now)
        assert len(results) == 1
        assert isinstance(results[0], TransitionResult)
        assert results[0].new_status == EventStatus.LATE
        assert results[0].old_status == EventStatus.PREMATCH

    async def test_detect_removed_returns_removed_results(self, db):
        from live_coverage_bot.db.repository import EventRepository
        from live_coverage_bot.core.tracker import EventLifecycleTracker
        from live_coverage_bot.models.events import EventStatus, TrackedEvent
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo)
        event = TrackedEvent(
            betpawa_event_id="200",
            home_team="C",
            away_team="D",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 16, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="2")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        await repo.insert_event(event)

        now = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        results = await tracker.detect_removed(set(), now=now)
        assert len(results) == 1
        assert isinstance(results[0], RemovedResult)
        assert results[0].old_status == EventStatus.PREMATCH
