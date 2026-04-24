"""Tests for event lifecycle models."""

from datetime import UTC, datetime

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.models.events import EventStatus, StateChange, TrackedEvent


class TestEventStatus:
    def test_all_statuses_exist(self):
        assert EventStatus.PREMATCH == "PREMATCH"
        assert EventStatus.LIVE == "LIVE"
        assert EventStatus.LATE == "LATE"
        assert EventStatus.NEVER_LIVE == "NEVER_LIVE"
        assert EventStatus.REMOVED == "REMOVED"

    def test_terminal_states(self):
        assert EventStatus.LIVE.is_terminal
        assert EventStatus.NEVER_LIVE.is_terminal
        assert not EventStatus.REMOVED.is_terminal  # REMOVED is no longer terminal — we keep watching
        assert not EventStatus.PREMATCH.is_terminal
        assert not EventStatus.LATE.is_terminal

    def test_unmonitored_is_terminal(self):
        assert EventStatus.UNMONITORED.is_terminal is True

    def test_unmonitored_value(self):
        assert EventStatus.UNMONITORED.value == "UNMONITORED"

    def test_existing_terminal_states_unchanged(self):
        assert EventStatus.LIVE.is_terminal is True
        assert EventStatus.NEVER_LIVE.is_terminal is True

    def test_non_terminal_states_unchanged(self):
        assert EventStatus.PREMATCH.is_terminal is False
        assert EventStatus.LATE.is_terminal is False
        assert EventStatus.REMOVED.is_terminal is False


class TestTrackedEvent:
    def test_create_from_upcoming(self):
        provider_ids = [ProviderID(type=ProviderType.SPORTRADAR, id="12345")]
        event = TrackedEvent(
            betpawa_event_id="99001",
            home_team="Arsenal",
            away_team="Chelsea",
            competition="England Premier League",
            country="England",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=provider_ids,
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        assert event.betpawa_event_id == "99001"
        assert event.status == EventStatus.PREMATCH
        assert event.first_seen_live is None
        assert event.transition_delay_sec is None
        assert event.slack_message_ts is None

    def test_provider_ids_json_serialization(self):
        provider_ids = [
            ProviderID(type=ProviderType.SPORTRADAR, id="12345"),
            ProviderID(type=ProviderType.GENIUSSPORTS, id="67890"),
        ]
        event = TrackedEvent(
            betpawa_event_id="99001",
            home_team="Arsenal",
            away_team="Chelsea",
            competition="England Premier League",
            country="England",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=provider_ids,
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        json_str = event.provider_ids_json
        assert '"SPORTRADAR"' in json_str
        assert '"12345"' in json_str
        assert '"GENIUSSPORTS"' in json_str
        assert '"67890"' in json_str


class TestLateReasonFields:
    def test_tracked_event_late_reason_default_none(self):
        from live_coverage_bot.models.events import TrackedEvent, EventStatus
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        event = TrackedEvent(
            betpawa_event_id="1", home_team="A", away_team="B",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        assert event.late_reason is None
        assert event.live_minute is None

    def test_tracked_event_late_reason_set(self):
        from live_coverage_bot.models.events import TrackedEvent, EventStatus
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        event = TrackedEvent(
            betpawa_event_id="1", home_team="A", away_team="B",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            late_reason="COVERAGE_LATE",
            live_minute=23,
        )
        assert event.late_reason == "COVERAGE_LATE"
        assert event.live_minute == 23

    def test_transition_result_live_minute(self):
        from live_coverage_bot.models.events import TransitionResult, EventStatus, TrackedEvent
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        event = TrackedEvent(
            betpawa_event_id="1", home_team="A", away_team="B",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        result = TransitionResult(
            event=event, old_status=EventStatus.LATE,
            new_status=EventStatus.LIVE, live_minute=12,
        )
        assert result.live_minute == 12


class TestStateChange:
    def test_create_state_change(self):
        change = StateChange(
            event_id=1,
            old_status=EventStatus.PREMATCH,
            new_status=EventStatus.LATE,
            changed_at=datetime(2026, 4, 15, 15, 5, tzinfo=UTC),
            details="5min past kickoff",
        )
        assert change.event_id == 1
        assert change.old_status == EventStatus.PREMATCH
        assert change.new_status == EventStatus.LATE
        assert change.details == "5min past kickoff"
