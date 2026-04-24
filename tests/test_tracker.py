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


class TestMarkUnmonitored:
    async def test_marks_past_kickoff_events_as_unmonitored(self, tracker, repo):
        kickoff_past = datetime(2026, 4, 18, 15, 0, tzinfo=UTC)
        now_register = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff_past,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_register,
        )

        downtime_start = datetime(2026, 4, 18, 14, 0, tzinfo=UTC)
        now_restart = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)

        count = await tracker.mark_unmonitored(
            downtime_start=downtime_start, now=now_restart
        )
        assert count == 1

        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.UNMONITORED

    async def test_leaves_future_kickoff_events_alone(self, tracker, repo):
        kickoff_future = datetime(2026, 4, 21, 15, 0, tzinfo=UTC)
        now_register = datetime(2026, 4, 20, 8, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99002",
                    "home_team": "Liverpool",
                    "away_team": "Man City",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff_future,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_register,
        )

        downtime_start = datetime(2026, 4, 20, 8, 30, tzinfo=UTC)
        now_restart = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)

        count = await tracker.mark_unmonitored(
            downtime_start=downtime_start, now=now_restart
        )
        assert count == 0

        event = await repo.get_by_betpawa_id("99002")
        assert event.status == EventStatus.PREMATCH

    async def test_marks_late_events_as_unmonitored(self, tracker, repo):
        kickoff = datetime(2026, 4, 18, 15, 0, tzinfo=UTC)
        now_register = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99003",
                    "home_team": "Spurs",
                    "away_team": "West Ham",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_register,
        )
        # Transition to LATE before shutdown
        now_late = kickoff + timedelta(minutes=6)
        await tracker.check_transitions(set(), now=now_late)

        downtime_start = datetime(2026, 4, 18, 15, 10, tzinfo=UTC)
        now_restart = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)

        count = await tracker.mark_unmonitored(
            downtime_start=downtime_start, now=now_restart
        )
        assert count == 1

        event = await repo.get_by_betpawa_id("99003")
        assert event.status == EventStatus.UNMONITORED

    async def test_records_state_change_for_unmonitored(self, tracker, repo):
        kickoff = datetime(2026, 4, 18, 15, 0, tzinfo=UTC)
        now_register = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99004",
                    "home_team": "Brighton",
                    "away_team": "Everton",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_register,
        )

        downtime_start = datetime(2026, 4, 18, 14, 0, tzinfo=UTC)
        now_restart = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)

        await tracker.mark_unmonitored(
            downtime_start=downtime_start, now=now_restart
        )

        event = await repo.get_by_betpawa_id("99004")
        changes = await repo.get_state_changes(event.id)
        unmonitored_change = [c for c in changes if c.new_status == EventStatus.UNMONITORED]
        assert len(unmonitored_change) == 1
        assert "offline" in unmonitored_change[0].details.lower()

    async def test_returns_zero_when_no_stale_events(self, tracker, repo):
        downtime_start = datetime(2026, 4, 20, 8, 0, tzinfo=UTC)
        now_restart = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)

        count = await tracker.mark_unmonitored(
            downtime_start=downtime_start, now=now_restart
        )
        assert count == 0


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


class TestLateReasonClassification:
    async def test_late_to_live_coverage_late(self, db):
        """Minute > threshold → COVERAGE_LATE."""
        from live_coverage_bot.clients.models import LiveEvent

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo, grace_period_minutes=5, hard_timeout_minutes=90, coverage_late_minute_threshold=5)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        await tracker.register_prematch_events([{"betpawa_event_id": "99001", "home_team": "Arsenal", "away_team": "Chelsea", "competition": "EPL", "country": "England", "scheduled_kickoff": kickoff, "provider_ids": _make_provider_ids()}], now=datetime(2026, 4, 15, 12, 0, tzinfo=UTC))
        await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=6))
        live_event = LiveEvent(event_id="bp:99001", home_team="Arsenal", away_team="Chelsea", competition_id="1", competition_name="EPL", minute="23", home_score=0, away_score=0, start_time=kickoff)
        transitions = await tracker.check_transitions({"99001"}, now=kickoff + timedelta(minutes=25), live_events_map={"99001": live_event})
        assert len(transitions) == 1
        assert transitions[0].new_status == EventStatus.LIVE
        assert transitions[0].live_minute == 23
        event = await repo.get_by_betpawa_id("99001")
        assert event.late_reason == "COVERAGE_LATE"
        assert event.live_minute == 23

    async def test_late_to_live_match_delayed(self, db):
        """Minute <= threshold → MATCH_DELAYED."""
        from live_coverage_bot.clients.models import LiveEvent

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo, grace_period_minutes=5, hard_timeout_minutes=90, coverage_late_minute_threshold=5)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        await tracker.register_prematch_events([{"betpawa_event_id": "99002", "home_team": "Liverpool", "away_team": "City", "competition": "EPL", "country": "England", "scheduled_kickoff": kickoff, "provider_ids": _make_provider_ids()}], now=datetime(2026, 4, 15, 12, 0, tzinfo=UTC))
        await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=6))
        live_event = LiveEvent(event_id="bp:99002", home_team="Liverpool", away_team="City", competition_id="1", competition_name="EPL", minute="2", home_score=0, away_score=0, start_time=kickoff)
        transitions = await tracker.check_transitions({"99002"}, now=kickoff + timedelta(minutes=12), live_events_map={"99002": live_event})
        assert len(transitions) == 1
        assert transitions[0].live_minute == 2
        event = await repo.get_by_betpawa_id("99002")
        assert event.late_reason == "MATCH_DELAYED"
        assert event.live_minute == 2

    async def test_prematch_to_live_coverage_late(self, db):
        """PREMATCH → LIVE with minute > threshold → COVERAGE_LATE."""
        from live_coverage_bot.clients.models import LiveEvent

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo, grace_period_minutes=5, hard_timeout_minutes=90, coverage_late_minute_threshold=5)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        await tracker.register_prematch_events([{"betpawa_event_id": "99003", "home_team": "Spurs", "away_team": "West Ham", "competition": "EPL", "country": "England", "scheduled_kickoff": kickoff, "provider_ids": _make_provider_ids()}], now=datetime(2026, 4, 15, 12, 0, tzinfo=UTC))
        live_event = LiveEvent(event_id="bp:99003", home_team="Spurs", away_team="West Ham", competition_id="1", competition_name="EPL", minute="12", home_score=0, away_score=0, start_time=kickoff)
        transitions = await tracker.check_transitions({"99003"}, now=kickoff + timedelta(minutes=3), live_events_map={"99003": live_event})
        assert len(transitions) == 1
        assert transitions[0].new_status == EventStatus.LIVE
        event = await repo.get_by_betpawa_id("99003")
        assert event.late_reason == "COVERAGE_LATE"
        assert event.live_minute == 12

    async def test_prematch_to_live_on_time_no_reason(self, db):
        """PREMATCH → LIVE with minute <= threshold → no late_reason."""
        from live_coverage_bot.clients.models import LiveEvent

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo, grace_period_minutes=5, hard_timeout_minutes=90, coverage_late_minute_threshold=5)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        await tracker.register_prematch_events([{"betpawa_event_id": "99004", "home_team": "Brighton", "away_team": "Everton", "competition": "EPL", "country": "England", "scheduled_kickoff": kickoff, "provider_ids": _make_provider_ids()}], now=datetime(2026, 4, 15, 12, 0, tzinfo=UTC))
        live_event = LiveEvent(event_id="bp:99004", home_team="Brighton", away_team="Everton", competition_id="1", competition_name="EPL", minute="1", home_score=0, away_score=0, start_time=kickoff)
        transitions = await tracker.check_transitions({"99004"}, now=kickoff + timedelta(minutes=1), live_events_map={"99004": live_event})
        assert len(transitions) == 1
        event = await repo.get_by_betpawa_id("99004")
        assert event.late_reason is None
        assert event.live_minute == 1

    async def test_late_to_live_no_map_no_classification(self, db):
        """Without live_events_map, live_minute and late_reason stay None."""
        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo, grace_period_minutes=5, hard_timeout_minutes=90, coverage_late_minute_threshold=5)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        await tracker.register_prematch_events([{"betpawa_event_id": "99005", "home_team": "A", "away_team": "B", "competition": "EPL", "country": "England", "scheduled_kickoff": kickoff, "provider_ids": _make_provider_ids()}], now=datetime(2026, 4, 15, 12, 0, tzinfo=UTC))
        await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=6))
        transitions = await tracker.check_transitions({"99005"}, now=kickoff + timedelta(minutes=12))
        assert len(transitions) == 1
        event = await repo.get_by_betpawa_id("99005")
        assert event.late_reason is None
        assert event.live_minute is None


class TestKickoffReschedule:
    async def test_late_reverts_to_prematch_on_reschedule(self, db):
        from live_coverage_bot.clients.models import UpcomingEvent

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo, grace_period_minutes=5, hard_timeout_minutes=90, coverage_late_minute_threshold=5)
        kickoff = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events([{"betpawa_event_id": "99010", "home_team": "TeamA", "away_team": "TeamB", "competition": "U19 Elit A", "country": "Turkey", "scheduled_kickoff": kickoff, "provider_ids": _make_provider_ids()}], now=datetime(2026, 4, 15, 10, 0, tzinfo=UTC))
        await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=6))
        event = await repo.get_by_betpawa_id("99010")
        assert event.status == EventStatus.LATE

        new_kickoff = datetime(2026, 4, 15, 13, 0, tzinfo=UTC)
        prematch_event = UpcomingEvent(event_id="99010", home_team="TeamA", away_team="TeamB", competition_name="U19 Elit A", country_name="Turkey", start_time=new_kickoff, provider_ids=_make_provider_ids())
        transitions = await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=7), prematch_events_map={"99010": prematch_event})
        assert len(transitions) == 1
        t = transitions[0]
        assert t.old_status == EventStatus.LATE
        assert t.new_status == EventStatus.PREMATCH
        assert "12:00" in t.details
        assert "13:00" in t.details
        event = await repo.get_by_betpawa_id("99010")
        assert event.status == EventStatus.PREMATCH
        assert event.scheduled_kickoff == new_kickoff
        assert event.late_reason == "KICKOFF_RESCHEDULED"

    async def test_late_no_reschedule_if_same_kickoff(self, db):
        from live_coverage_bot.clients.models import UpcomingEvent

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo, grace_period_minutes=5, hard_timeout_minutes=90, coverage_late_minute_threshold=5)
        kickoff = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events([{"betpawa_event_id": "99011", "home_team": "TeamC", "away_team": "TeamD", "competition": "Test", "country": "Test", "scheduled_kickoff": kickoff, "provider_ids": _make_provider_ids()}], now=datetime(2026, 4, 15, 10, 0, tzinfo=UTC))
        await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=6))
        prematch_event = UpcomingEvent(event_id="99011", home_team="TeamC", away_team="TeamD", competition_name="Test", country_name="Test", start_time=kickoff, provider_ids=_make_provider_ids())
        transitions = await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=7), prematch_events_map={"99011": prematch_event})
        assert len(transitions) == 0
        event = await repo.get_by_betpawa_id("99011")
        assert event.status == EventStatus.LATE

    async def test_late_no_prematch_map_skips_reschedule(self, db):
        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo, grace_period_minutes=5, hard_timeout_minutes=90, coverage_late_minute_threshold=5)
        kickoff = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events([{"betpawa_event_id": "99012", "home_team": "TeamE", "away_team": "TeamF", "competition": "Test", "country": "Test", "scheduled_kickoff": kickoff, "provider_ids": _make_provider_ids()}], now=datetime(2026, 4, 15, 10, 0, tzinfo=UTC))
        await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=6))
        transitions = await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=7))
        assert len(transitions) == 0

    async def test_reschedule_then_normal_live(self, db):
        """Full lifecycle: PREMATCH → LATE → PREMATCH (reschedule) → LIVE."""
        from live_coverage_bot.clients.models import LiveEvent, UpcomingEvent

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo, grace_period_minutes=5, hard_timeout_minutes=90, coverage_late_minute_threshold=5)
        old_kickoff = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        new_kickoff = datetime(2026, 4, 15, 13, 0, tzinfo=UTC)
        await tracker.register_prematch_events([{"betpawa_event_id": "99013", "home_team": "TeamG", "away_team": "TeamH", "competition": "Test", "country": "Test", "scheduled_kickoff": old_kickoff, "provider_ids": _make_provider_ids()}], now=datetime(2026, 4, 15, 10, 0, tzinfo=UTC))
        await tracker.check_transitions(set(), now=old_kickoff + timedelta(minutes=6))
        prematch_event = UpcomingEvent(event_id="99013", home_team="TeamG", away_team="TeamH", competition_name="Test", country_name="Test", start_time=new_kickoff, provider_ids=_make_provider_ids())
        await tracker.check_transitions(set(), now=old_kickoff + timedelta(minutes=7), prematch_events_map={"99013": prematch_event})
        live_event = LiveEvent(event_id="bp:99013", home_team="TeamG", away_team="TeamH", competition_id="1", competition_name="Test", minute="1", home_score=0, away_score=0, start_time=new_kickoff)
        transitions = await tracker.check_transitions({"99013"}, now=new_kickoff + timedelta(minutes=1), live_events_map={"99013": live_event})
        assert len(transitions) == 1
        assert transitions[0].new_status == EventStatus.LIVE
        event = await repo.get_by_betpawa_id("99013")
        assert event.status == EventStatus.LIVE
        assert event.transition_delay_sec == 60
        assert event.live_minute == 1
        changes = await repo.get_state_changes(event.id)
        statuses = [(c.old_status, c.new_status) for c in changes]
        assert (EventStatus.PREMATCH, EventStatus.LATE) in statuses
        assert (EventStatus.LATE, EventStatus.PREMATCH) in statuses
        assert (EventStatus.PREMATCH, EventStatus.LIVE) in statuses
