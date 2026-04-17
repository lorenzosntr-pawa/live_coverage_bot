"""Tests for the market snapshotter."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.config.models import MarketsConfig
from live_coverage_bot.core.market_snapshotter import MarketSnapshotter
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.models.markets import (
    Market,
    MarketRow,
    Selection,
    SnapshotPhase,
)


@pytest.fixture
def event_repo(db: Database) -> EventRepository:
    return EventRepository(db)


@pytest.fixture
def market_repo(db: Database) -> MarketRepository:
    return MarketRepository(db)


@pytest.fixture
def markets_config() -> MarketsConfig:
    return MarketsConfig()


@pytest.fixture
async def prematch_event_id(event_repo):
    event = TrackedEvent(
        betpawa_event_id="99001",
        home_team="A", away_team="B",
        competition="X", country="Y",
        scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
        status=EventStatus.PREMATCH,
        provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
        first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
    )
    return await event_repo.insert_event(event)


def _make_markets():
    return [Market(
        market_type_id="3743", market_type_name="1X2 - FT", priority=1,
        rows=[MarketRow(row_id="r1", handicap=None, selections=[
            Selection(price_id="p1", name="1", type_id="3744", price=2.0, suspended=False),
            Selection(price_id="p2", name="X", type_id="3745", price=3.3, suspended=False),
            Selection(price_id="p3", name="2", type_id="3746", price=4.0, suspended=False),
        ])],
    )]


class TestPhaseWindow:
    async def test_detects_prematch_60_window(self, event_repo, market_repo, markets_config, prematch_event_id):
        mock_betpawa = AsyncMock()
        mock_betpawa.get_event_markets.return_value = _make_markets()

        snapshotter = MarketSnapshotter(event_repo, market_repo, mock_betpawa, markets_config)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now = kickoff - timedelta(minutes=58)

        await snapshotter.run_cycle(now=now, live_transition_event_ids=set())

        phases = await market_repo.phases_taken_for_event(prematch_event_id)
        assert SnapshotPhase.PREMATCH_60 in phases

    async def test_skips_outside_window(self, event_repo, market_repo, markets_config, prematch_event_id):
        mock_betpawa = AsyncMock()
        mock_betpawa.get_event_markets.return_value = _make_markets()

        snapshotter = MarketSnapshotter(event_repo, market_repo, mock_betpawa, markets_config)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now = kickoff - timedelta(minutes=40)

        await snapshotter.run_cycle(now=now, live_transition_event_ids=set())

        phases = await market_repo.phases_taken_for_event(prematch_event_id)
        assert phases == set()

    async def test_skips_already_taken_phase(self, event_repo, market_repo, markets_config, prematch_event_id):
        mock_betpawa = AsyncMock()
        mock_betpawa.get_event_markets.return_value = _make_markets()

        snapshotter = MarketSnapshotter(event_repo, market_repo, mock_betpawa, markets_config)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now = kickoff - timedelta(minutes=58)

        await snapshotter.run_cycle(now=now, live_transition_event_ids=set())
        await snapshotter.run_cycle(now=now, live_transition_event_ids=set())

        assert mock_betpawa.get_event_markets.call_count == 1

    async def test_takes_live_snapshot_on_transition(self, event_repo, market_repo, markets_config, prematch_event_id):
        mock_betpawa = AsyncMock()
        mock_betpawa.get_event_markets.return_value = _make_markets()

        snapshotter = MarketSnapshotter(event_repo, market_repo, mock_betpawa, markets_config)
        now = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)

        await snapshotter.run_cycle(
            now=now,
            live_transition_event_ids={"99001"},
        )

        phases = await market_repo.phases_taken_for_event(prematch_event_id)
        assert SnapshotPhase.LIVE_0 in phases

    async def test_disabled_does_nothing(self, event_repo, market_repo, prematch_event_id):
        mock_betpawa = AsyncMock()
        disabled_config = MarketsConfig(enabled=False)

        snapshotter = MarketSnapshotter(event_repo, market_repo, mock_betpawa, disabled_config)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        await snapshotter.run_cycle(now=kickoff - timedelta(minutes=58),
                                    live_transition_event_ids={"99001"})

        mock_betpawa.get_event_markets.assert_not_called()
        phases = await market_repo.phases_taken_for_event(prematch_event_id)
        assert phases == set()

    async def test_fetch_error_skips_phase(self, event_repo, market_repo, markets_config, prematch_event_id):
        from live_coverage_bot.clients.betpawa import BetPawaError

        mock_betpawa = AsyncMock()
        mock_betpawa.get_event_markets.side_effect = BetPawaError("API 500")

        snapshotter = MarketSnapshotter(event_repo, market_repo, mock_betpawa, markets_config)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now = kickoff - timedelta(minutes=58)

        await snapshotter.run_cycle(now=now, live_transition_event_ids=set())

        phases = await market_repo.phases_taken_for_event(prematch_event_id)
        assert SnapshotPhase.PREMATCH_60 not in phases


def _live_event(first_seen_live: datetime) -> TrackedEvent:
    return TrackedEvent(
        id=1,
        betpawa_event_id="700",
        home_team="A", away_team="B", competition="Test",
        scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
        status=EventStatus.LIVE,
        provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
        first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        first_seen_live=first_seen_live,
    )


class TestLiveFollowUpPhases:
    def test_live_2_due_after_2_minutes(self):
        config = MarketsConfig()
        snapshotter = MarketSnapshotter(MagicMock(), MagicMock(), MagicMock(), config)
        event = _live_event(first_seen_live=datetime(2026, 4, 15, 15, 0, tzinfo=UTC))
        now = datetime(2026, 4, 15, 15, 2, 30, tzinfo=UTC)
        phases = snapshotter._live_follow_up_phases_due(event, now)
        assert SnapshotPhase.LIVE_2 in phases
        assert SnapshotPhase.LIVE_5 not in phases

    def test_live_5_due_after_5_minutes(self):
        config = MarketsConfig()
        snapshotter = MarketSnapshotter(MagicMock(), MagicMock(), MagicMock(), config)
        event = _live_event(first_seen_live=datetime(2026, 4, 15, 15, 0, tzinfo=UTC))
        now = datetime(2026, 4, 15, 15, 5, 30, tzinfo=UTC)
        phases = snapshotter._live_follow_up_phases_due(event, now)
        assert SnapshotPhase.LIVE_2 in phases
        assert SnapshotPhase.LIVE_5 in phases

    def test_no_phases_before_2_minutes(self):
        config = MarketsConfig()
        snapshotter = MarketSnapshotter(MagicMock(), MagicMock(), MagicMock(), config)
        event = _live_event(first_seen_live=datetime(2026, 4, 15, 15, 0, tzinfo=UTC))
        now = datetime(2026, 4, 15, 15, 1, 0, tzinfo=UTC)
        phases = snapshotter._live_follow_up_phases_due(event, now)
        assert len(phases) == 0

    def test_no_phases_for_non_live_event(self):
        config = MarketsConfig()
        snapshotter = MarketSnapshotter(MagicMock(), MagicMock(), MagicMock(), config)
        event = _live_event(first_seen_live=datetime(2026, 4, 15, 15, 0, tzinfo=UTC))
        event.status = EventStatus.PREMATCH
        now = datetime(2026, 4, 15, 15, 5, 30, tzinfo=UTC)
        phases = snapshotter._live_follow_up_phases_due(event, now)
        assert len(phases) == 0
