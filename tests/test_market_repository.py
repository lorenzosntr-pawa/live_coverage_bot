"""Tests for the market repository."""

import json
from datetime import UTC, datetime

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.models.markets import (
    ComparisonDetails,
    Market,
    MarketComparison,
    MarketRow,
    MarketSnapshot,
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
async def event_id(event_repo) -> int:
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


def _sample_market() -> Market:
    return Market(
        market_type_id="3743",
        market_type_name="1X2 - FT",
        priority=1,
        rows=[MarketRow(
            row_id="r1", handicap=None,
            selections=[
                Selection(price_id="p1", name="1", type_id="3744", price=2.10, suspended=False),
                Selection(price_id="p2", name="X", type_id="3745", price=3.50, suspended=False),
                Selection(price_id="p3", name="2", type_id="3746", price=3.80, suspended=False),
            ],
        )],
    )


class TestSnapshotOps:
    async def test_insert_and_fetch_snapshot(self, market_repo, event_id):
        snap = MarketSnapshot(
            event_id=event_id,
            phase=SnapshotPhase.PREMATCH_60,
            taken_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
            markets=[_sample_market()],
            total_market_count=1,
            total_selection_count=3,
            suspended_count=0,
        )
        row_id = await market_repo.insert_snapshot(snap, fetch_duration_ms=120)
        assert row_id > 0

        latest_prematch = await market_repo.get_latest_prematch_snapshot(event_id)
        assert latest_prematch is not None
        assert latest_prematch.phase == SnapshotPhase.PREMATCH_60
        assert len(latest_prematch.markets) == 1
        assert latest_prematch.markets[0].market_type_id == "3743"

    async def test_latest_prematch_prefers_closest_phase(self, market_repo, event_id):
        base_markets = [_sample_market()]
        await market_repo.insert_snapshot(
            MarketSnapshot(
                event_id=event_id, phase=SnapshotPhase.PREMATCH_60,
                taken_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
                markets=base_markets, total_market_count=1,
                total_selection_count=3, suspended_count=0,
            ),
        )
        await market_repo.insert_snapshot(
            MarketSnapshot(
                event_id=event_id, phase=SnapshotPhase.PREMATCH_1,
                taken_at=datetime(2026, 4, 15, 14, 59, tzinfo=UTC),
                markets=base_markets, total_market_count=1,
                total_selection_count=3, suspended_count=0,
            ),
        )

        latest = await market_repo.get_latest_prematch_snapshot(event_id)
        assert latest is not None
        assert latest.phase == SnapshotPhase.PREMATCH_1

    async def test_phases_already_taken(self, market_repo, event_id):
        await market_repo.insert_snapshot(
            MarketSnapshot(
                event_id=event_id, phase=SnapshotPhase.PREMATCH_60,
                taken_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
                markets=[_sample_market()],
                total_market_count=1, total_selection_count=3, suspended_count=0,
            ),
        )
        taken = await market_repo.phases_taken_for_event(event_id)
        assert SnapshotPhase.PREMATCH_60 in taken
        assert SnapshotPhase.PREMATCH_1 not in taken

    async def test_unique_constraint_per_phase(self, market_repo, event_id):
        snap = MarketSnapshot(
            event_id=event_id, phase=SnapshotPhase.PREMATCH_60,
            taken_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
            markets=[_sample_market()],
            total_market_count=1, total_selection_count=3, suspended_count=0,
        )
        await market_repo.insert_snapshot(snap)
        with pytest.raises(Exception):
            await market_repo.insert_snapshot(snap)


class TestComparisonOps:
    async def test_insert_comparison(self, market_repo, event_id):
        cmp = MarketComparison(
            event_id=event_id,
            compared_at=datetime(2026, 4, 15, 15, 1, tzinfo=UTC),
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0,
            markets_dropped=53,
            markets_kept=34,
            retention_pct=39.1,
            dropped_key_markets=["Both Teams To Score - FT"],
            max_odds_shift_pct=16.7,
            triggered_alert=True,
            details=ComparisonDetails(dropped=[], added=[], odds_shifts=[]),
        )
        row_id = await market_repo.insert_comparison(cmp)
        assert row_id > 0

        fetched = await market_repo.get_comparison_for_event(event_id)
        assert fetched is not None
        assert fetched.retention_pct == 39.1
        assert fetched.triggered_alert
        assert "Both Teams To Score - FT" in fetched.dropped_key_markets

    async def test_get_comparisons_in_date_range(self, market_repo, event_id):
        cmp = MarketComparison(
            event_id=event_id,
            compared_at=datetime(2026, 4, 15, 15, 1, tzinfo=UTC),
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0, markets_dropped=10, markets_kept=5,
            retention_pct=33.3, dropped_key_markets=[],
            max_odds_shift_pct=5.0, triggered_alert=False,
            details=ComparisonDetails(dropped=[], added=[], odds_shifts=[]),
        )
        await market_repo.insert_comparison(cmp)

        start = datetime(2026, 4, 14, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        rows = await market_repo.get_comparisons_in_date_range(start, end)
        assert len(rows) == 1
