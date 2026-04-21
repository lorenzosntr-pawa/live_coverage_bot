"""Tests for the monitoring loop."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType, UpcomingEvent
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
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus


@pytest.fixture
def settings():
    return Settings(
        polling=PollingConfig(live_interval_seconds=1, prematch_interval_seconds=5),
        thresholds=ThresholdConfig(grace_period_minutes=5, hard_timeout_minutes=90),
        betpawa=BetPawaConfig(),
        slack=SlackConfig(bot_token="xoxb-test", channel_id="C123"),
        database=DatabaseConfig(path=":memory:"),
        reporting=ReportingConfig(),
        _env_file=None,
    )


class TestPollCycleLogic:
    async def test_new_prematch_event_gets_registered(self, db, settings):
        repo = EventRepository(db)
        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo
        loop._market_repo = MarketRepository(db)

        from live_coverage_bot.core.tracker import EventLifecycleTracker
        loop._tracker = EventLifecycleTracker(
            repo,
            grace_period_minutes=settings.thresholds.grace_period_minutes,
            hard_timeout_minutes=settings.thresholds.hard_timeout_minutes,
        )

        mock_betpawa = AsyncMock()
        mock_betpawa.get_upcoming_events.return_value = [
            UpcomingEvent(
                event_id="99001",
                home_team="Arsenal",
                away_team="Chelsea",
                competition_name="EPL",
                country_name="England",
                start_time=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
                provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
            )
        ]
        mock_betpawa.get_live_events.return_value = []

        mock_slack = AsyncMock()

        now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await loop._poll_cycle(mock_betpawa, mock_slack, now=now, force_prematch=True)

        event = await repo.get_by_betpawa_id("99001")
        assert event is not None
        assert event.status == EventStatus.PREMATCH

    async def test_late_event_triggers_slack_alert(self, db, settings):
        repo = EventRepository(db)
        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo
        loop._market_repo = MarketRepository(db)

        from live_coverage_bot.core.tracker import EventLifecycleTracker
        loop._tracker = EventLifecycleTracker(
            repo,
            grace_period_minutes=settings.thresholds.grace_period_minutes,
            hard_timeout_minutes=settings.thresholds.hard_timeout_minutes,
        )

        mock_betpawa = AsyncMock()
        mock_betpawa.get_upcoming_events.return_value = [
            UpcomingEvent(
                event_id="99001",
                home_team="Arsenal",
                away_team="Chelsea",
                competition_name="EPL",
                country_name="England",
                start_time=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
                provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
            )
        ]
        mock_betpawa.get_live_events.return_value = []

        mock_slack = AsyncMock()
        mock_slack.post_alert.return_value = "1234.5678"

        now_reg = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await loop._poll_cycle(mock_betpawa, mock_slack, now=now_reg, force_prematch=True)

        now_late = datetime(2026, 4, 15, 15, 6, tzinfo=UTC)
        mock_betpawa.get_live_events.return_value = []
        await loop._poll_cycle(mock_betpawa, mock_slack, now=now_late)

        mock_slack.post_alert.assert_called_once()
        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.LATE
        assert event.slack_message_ts == "1234.5678"

    async def test_prematch_event_stores_competition_id(self, db, settings):
        repo = EventRepository(db)
        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo
        loop._market_repo = MarketRepository(db)

        from live_coverage_bot.core.tracker import EventLifecycleTracker
        loop._tracker = EventLifecycleTracker(
            repo,
            grace_period_minutes=settings.thresholds.grace_period_minutes,
            hard_timeout_minutes=settings.thresholds.hard_timeout_minutes,
        )

        mock_betpawa = AsyncMock()
        mock_betpawa.get_upcoming_events.return_value = [
            UpcomingEvent(
                event_id="99001",
                home_team="Arsenal",
                away_team="Chelsea",
                competition_name="Premier League",
                competition_id="11965",
                country_name="England",
                start_time=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
                provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
            )
        ]
        mock_betpawa.get_live_events.return_value = []

        mock_slack = AsyncMock()

        now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await loop._poll_cycle(mock_betpawa, mock_slack, now=now, force_prematch=True)

        event = await repo.get_by_betpawa_id("99001")
        assert event is not None
        assert event.competition_id == "11965"
