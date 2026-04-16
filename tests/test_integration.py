"""Integration smoke test — full cycle with in-memory DB and mocked APIs."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from live_coverage_bot.clients.models import (
    LiveEvent,
    ProviderID,
    ProviderType,
    UpcomingEvent,
)
from live_coverage_bot.config.models import (
    BetPawaConfig,
    DatabaseConfig,
    PollingConfig,
    ReportingConfig,
    Settings,
    SlackConfig,
    ThresholdConfig,
)
from live_coverage_bot.core.loop import MonitoringLoop
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus


@pytest.fixture
def settings():
    return Settings(
        polling=PollingConfig(live_interval_seconds=1, prematch_interval_seconds=1),
        thresholds=ThresholdConfig(grace_period_minutes=5, hard_timeout_minutes=90),
        betpawa=BetPawaConfig(),
        slack=SlackConfig(bot_token="xoxb-test", channel_id="C123"),
        database=DatabaseConfig(path=":memory:"),
        reporting=ReportingConfig(),
        _env_file=None,
    )


class TestFullLifecycle:
    async def test_prematch_late_live_cycle(self, db, settings):
        repo = EventRepository(db)
        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo

        from live_coverage_bot.core.tracker import EventLifecycleTracker
        loop._tracker = EventLifecycleTracker(repo, grace_period_minutes=5, hard_timeout_minutes=90)

        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        pids = [ProviderID(type=ProviderType.SPORTRADAR, id="12345")]

        mock_betpawa = AsyncMock()
        mock_slack = AsyncMock()
        mock_slack.post_alert.return_value = "ts_1234"

        mock_betpawa.get_upcoming_events.return_value = [
            UpcomingEvent(
                event_id="99001", home_team="Arsenal", away_team="Chelsea",
                competition_name="EPL", country_name="England",
                start_time=kickoff, provider_ids=pids,
            )
        ]
        mock_betpawa.get_live_events.return_value = []
        await loop._poll_cycle(mock_betpawa, mock_slack, now=kickoff - timedelta(hours=2), force_prematch=True)

        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.PREMATCH

        await loop._poll_cycle(mock_betpawa, mock_slack, now=kickoff + timedelta(minutes=6))
        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.LATE
        assert event.slack_message_ts == "ts_1234"
        mock_slack.post_alert.assert_called_once()

        live_event = LiveEvent(
            event_id="bp:99001", home_team="Arsenal", away_team="Chelsea",
            competition_id="1", competition_name="EPL", country_name="England",
            minute="7", home_score=0, away_score=0, start_time=kickoff,
        )
        live_event._provider_ids_override = pids
        mock_betpawa.get_live_events.return_value = [live_event]

        await loop._poll_cycle(mock_betpawa, mock_slack, now=kickoff + timedelta(minutes=12))
        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.LIVE
        assert event.transition_delay_sec == 720
        mock_slack.update_message.assert_called_once()
        mock_slack.post_thread_reply.assert_called_once()

    async def test_weekly_report_generation(self, db, settings):
        repo = EventRepository(db)
        reporter = WeeklyReporter(repo, week_starts="tuesday")

        from live_coverage_bot.models.events import TrackedEvent
        kickoff = datetime(2026, 4, 14, 15, 0, tzinfo=UTC)
        await repo.insert_event(TrackedEvent(
            betpawa_event_id="1", home_team="A", away_team="B",
            competition="EPL", country="England",
            scheduled_kickoff=kickoff, status=EventStatus.LIVE,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="100")],
            first_seen_prematch=kickoff, first_seen_live=kickoff,
            transition_delay_sec=60,
        ))

        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        assert "Total prematch events tracked: 1" in summary

        csv_content = await reporter.generate_csv(start, end)
        assert "EPL" in csv_content
