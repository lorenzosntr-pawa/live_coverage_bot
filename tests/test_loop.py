"""Tests for the monitoring loop."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call
from pathlib import Path

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType, UpcomingEvent
from live_coverage_bot.config.models import (
    BetPawaConfig,
    DatabaseConfig,
    MarketsConfig,
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


def _make_loop_settings(**markets_overrides) -> Settings:
    markets_kwargs = {"enabled": True}
    markets_kwargs.update(markets_overrides)
    return Settings(
        polling=PollingConfig(),
        thresholds=ThresholdConfig(),
        slack=SlackConfig(bot_token="xoxb-test", channel_id="C123"),
        database=DatabaseConfig(path=":memory:"),
        reporting=ReportingConfig(day="tuesday", time="08:00", week_starts="tuesday"),
        markets=MarketsConfig(**markets_kwargs),
        _env_file=None,
    )


class TestSplitWeeklyReport:
    async def test_posts_two_separate_messages(self, db):
        """Weekly report sends coverage message + market message as two separate posts."""
        settings = _make_loop_settings()
        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = EventRepository(db)
        loop._market_repo = MarketRepository(db)

        mock_slack = AsyncMock()
        mock_slack.post_summary = AsyncMock(side_effect=["ts_coverage", "ts_market"])
        mock_slack.upload_file = AsyncMock()
        mock_slack._config = settings.slack

        # Tuesday 08:01 UTC triggers the report
        now = datetime(2026, 4, 14, 8, 1, tzinfo=UTC)

        with patch("live_coverage_bot.core.loop.WeeklyReporter") as MockReporter, \
             patch("live_coverage_bot.core.loop.MarketReporter") as MockMarketReporter:

            mock_reporter_inst = AsyncMock()
            mock_reporter_inst.compute_report_period = MagicMock(return_value=(
                datetime(2026, 4, 7, tzinfo=UTC),
                datetime(2026, 4, 13, 23, 59, 59, tzinfo=UTC),
            ))
            mock_reporter_inst.generate_slack_summary = AsyncMock(return_value="coverage summary")
            mock_reporter_inst.generate_csv = AsyncMock(return_value="csv,data\n1,2\n")
            MockReporter.return_value = mock_reporter_inst
            MockReporter.WEEKDAY_MAP = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}

            mock_market_inst = AsyncMock()
            mock_market_inst.generate_top_leagues_block = AsyncMock(return_value="top leagues block")
            mock_market_inst.generate_minimal_summary = AsyncMock(return_value="market summary")
            mock_market_inst.generate_aggregate_csv = AsyncMock(return_value="market,csv\na,b\n")
            mock_market_inst.generate_per_event_csv = AsyncMock(return_value="per,event\n")
            MockMarketReporter.return_value = mock_market_inst

            # Mock get_comparisons_in_date_range to return empty (no per-event CSVs)
            loop._market_repo.get_comparisons_in_date_range = AsyncMock(return_value=[])

            await loop._check_weekly_report(mock_slack, now)

        # Two separate post_summary calls
        assert mock_slack.post_summary.call_count == 2

        # First call: coverage summary + top leagues
        first_summary = mock_slack.post_summary.call_args_list[0][0][0]
        assert "coverage summary" in first_summary
        assert "top leagues block" in first_summary

        # Second call: minimal market summary
        second_summary = mock_slack.post_summary.call_args_list[1][0][0]
        assert "market summary" in second_summary

        # Two upload_file calls (one CSV each)
        assert mock_slack.upload_file.call_count == 2

    async def test_coverage_csv_threaded_to_coverage_message(self, db):
        """Coverage CSV is uploaded threaded to the coverage message ts."""
        settings = _make_loop_settings()
        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = EventRepository(db)
        loop._market_repo = MarketRepository(db)

        mock_slack = AsyncMock()
        mock_slack.post_summary = AsyncMock(side_effect=["ts_cov", "ts_mkt"])
        mock_slack.upload_file = AsyncMock()
        mock_slack._config = settings.slack

        now = datetime(2026, 4, 14, 8, 1, tzinfo=UTC)

        with patch("live_coverage_bot.core.loop.WeeklyReporter") as MockReporter, \
             patch("live_coverage_bot.core.loop.MarketReporter") as MockMarketReporter:

            mock_reporter_inst = AsyncMock()
            mock_reporter_inst.compute_report_period = MagicMock(return_value=(
                datetime(2026, 4, 7, tzinfo=UTC),
                datetime(2026, 4, 13, 23, 59, 59, tzinfo=UTC),
            ))
            mock_reporter_inst.generate_slack_summary = AsyncMock(return_value="cov")
            mock_reporter_inst.generate_csv = AsyncMock(return_value="csv_content")
            MockReporter.return_value = mock_reporter_inst
            MockReporter.WEEKDAY_MAP = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}

            mock_market_inst = AsyncMock()
            mock_market_inst.generate_top_leagues_block = AsyncMock(return_value="")
            mock_market_inst.generate_minimal_summary = AsyncMock(return_value="mkt")
            mock_market_inst.generate_aggregate_csv = AsyncMock(return_value="mkt_csv")
            mock_market_inst.generate_per_event_csv = AsyncMock(return_value="")
            MockMarketReporter.return_value = mock_market_inst

            loop._market_repo.get_comparisons_in_date_range = AsyncMock(return_value=[])

            await loop._check_weekly_report(mock_slack, now)

        # First upload_file call should use ts_cov as thread_ts
        first_upload_kwargs = mock_slack.upload_file.call_args_list[0]
        # Check keyword arg thread_ts
        assert first_upload_kwargs.kwargs.get("thread_ts") == "ts_cov"
