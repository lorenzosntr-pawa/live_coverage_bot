"""Tests for market alert league filtering."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.config.models import (
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
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.models.markets import SnapshotPhase


def _make_settings(**markets_overrides) -> Settings:
    markets_kwargs = {"enabled": True}
    markets_kwargs.update(markets_overrides)
    return Settings(
        polling=PollingConfig(),
        thresholds=ThresholdConfig(),
        slack=SlackConfig(bot_token="xoxb-test", channel_id="C123"),
        database=DatabaseConfig(path=":memory:"),
        reporting=ReportingConfig(),
        markets=MarketsConfig(**markets_kwargs),
        _env_file=None,
    )


async def _insert_test_event(
    repo: EventRepository, competition_id: str = "11965"
) -> TrackedEvent:
    """Insert a test event and return it with the DB-assigned id."""
    event = TrackedEvent(
        betpawa_event_id="99001",
        home_team="Arsenal",
        away_team="Chelsea",
        competition="Premier League",
        competition_id=competition_id,
        country="England",
        scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
        status=EventStatus.LIVE,
        provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
        first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        first_seen_live=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
    )
    row_id = await repo.insert_event(event)
    event.id = row_id
    return event


class TestMarketAlertLeagueFilter:
    async def test_skips_slack_when_competition_not_in_whitelist(self, db):
        """Market comparison is saved to DB but Slack alert is NOT sent."""
        settings = _make_settings(alert_competition_ids=["12097"])  # Serie A only
        repo = EventRepository(db)
        market_repo = MarketRepository(db)

        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo
        loop._market_repo = market_repo

        event = await _insert_test_event(repo, competition_id="11965")  # Premier League

        mock_slack = AsyncMock()
        now = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)

        with patch.object(loop, "_handle_initial_comparison", new_callable=AsyncMock) as mock_initial, \
             patch.object(loop, "_handle_followup_comparison", new_callable=AsyncMock) as mock_followup, \
             patch("live_coverage_bot.core.loop.compare_snapshots") as mock_compare, \
             patch.object(market_repo, "get_latest_prematch_snapshot", new_callable=AsyncMock) as mock_pre, \
             patch.object(market_repo, "get_snapshots_for_event", new_callable=AsyncMock) as mock_snaps, \
             patch.object(market_repo, "insert_comparison", new_callable=AsyncMock):

            # Set up mocks so comparison proceeds
            mock_pre_snap = AsyncMock()
            mock_pre_snap.total_market_count = 100
            mock_pre.return_value = mock_pre_snap

            mock_live_snap = AsyncMock()
            mock_live_snap.phase = SnapshotPhase.LIVE_0
            mock_snaps.return_value = [mock_live_snap]

            mock_cmp = AsyncMock()
            mock_cmp.snapshot_phase = None
            mock_compare.return_value = mock_cmp

            await loop._handle_market_comparison(mock_slack, event.id, SnapshotPhase.LIVE_0, now)

            # Comparison was saved to DB
            market_repo.insert_comparison.assert_called_once()
            # But Slack handlers were NOT called
            mock_initial.assert_not_called()
            mock_followup.assert_not_called()

    async def test_posts_slack_when_competition_in_whitelist(self, db):
        """Market comparison is saved AND Slack alert IS sent."""
        settings = _make_settings(alert_competition_ids=["11965"])  # Premier League
        repo = EventRepository(db)
        market_repo = MarketRepository(db)

        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo
        loop._market_repo = market_repo

        event = await _insert_test_event(repo, competition_id="11965")

        mock_slack = AsyncMock()
        now = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)

        with patch.object(loop, "_handle_initial_comparison", new_callable=AsyncMock) as mock_initial, \
             patch("live_coverage_bot.core.loop.compare_snapshots") as mock_compare, \
             patch.object(market_repo, "get_latest_prematch_snapshot", new_callable=AsyncMock) as mock_pre, \
             patch.object(market_repo, "get_snapshots_for_event", new_callable=AsyncMock) as mock_snaps, \
             patch.object(market_repo, "insert_comparison", new_callable=AsyncMock):

            mock_pre_snap = AsyncMock()
            mock_pre_snap.total_market_count = 100
            mock_pre.return_value = mock_pre_snap

            mock_live_snap = AsyncMock()
            mock_live_snap.phase = SnapshotPhase.LIVE_0
            mock_snaps.return_value = [mock_live_snap]

            mock_cmp = AsyncMock()
            mock_cmp.snapshot_phase = None
            mock_compare.return_value = mock_cmp

            await loop._handle_market_comparison(mock_slack, event.id, SnapshotPhase.LIVE_0, now)

            # Slack handler WAS called
            mock_initial.assert_called_once()

    async def test_empty_whitelist_alerts_everything(self, db):
        """Empty alert_competition_ids means no filtering — all alerts go through."""
        settings = _make_settings(alert_competition_ids=[])  # Empty = alert all
        repo = EventRepository(db)
        market_repo = MarketRepository(db)

        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo
        loop._market_repo = market_repo

        event = await _insert_test_event(repo, competition_id="99999")  # Random league

        mock_slack = AsyncMock()
        now = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)

        with patch.object(loop, "_handle_initial_comparison", new_callable=AsyncMock) as mock_initial, \
             patch("live_coverage_bot.core.loop.compare_snapshots") as mock_compare, \
             patch.object(market_repo, "get_latest_prematch_snapshot", new_callable=AsyncMock) as mock_pre, \
             patch.object(market_repo, "get_snapshots_for_event", new_callable=AsyncMock) as mock_snaps, \
             patch.object(market_repo, "insert_comparison", new_callable=AsyncMock):

            mock_pre_snap = AsyncMock()
            mock_pre_snap.total_market_count = 100
            mock_pre.return_value = mock_pre_snap

            mock_live_snap = AsyncMock()
            mock_live_snap.phase = SnapshotPhase.LIVE_0
            mock_snaps.return_value = [mock_live_snap]

            mock_cmp = AsyncMock()
            mock_cmp.snapshot_phase = None
            mock_compare.return_value = mock_cmp

            await loop._handle_market_comparison(mock_slack, event.id, SnapshotPhase.LIVE_0, now)

            # Slack handler called — empty whitelist means alert everything
            mock_initial.assert_called_once()
