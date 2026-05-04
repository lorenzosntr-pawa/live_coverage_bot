"""Tests for the market reporter (CSV + Slack summary)."""

import csv
import io
from datetime import UTC, datetime

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.core.market_reporter import MarketReporter
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.models.markets import (
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
def reporter(event_repo, market_repo) -> MarketReporter:
    return MarketReporter(event_repo, market_repo)


async def _seed(event_repo, market_repo):
    base = datetime(2026, 4, 14, 15, 0, tzinfo=UTC)
    event = TrackedEvent(
        betpawa_event_id="99001",
        home_team="Team A", away_team="Team B",
        competition="EPL", country="England",
        scheduled_kickoff=base, status=EventStatus.LIVE,
        provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
        first_seen_prematch=base, first_seen_live=base,
        transition_delay_sec=60,
    )
    eid = await event_repo.insert_event(event)

    market = Market(
        market_type_id="3743", market_type_name="1X2 - FT", priority=1,
        rows=[MarketRow(row_id="r1", handicap=None, selections=[
            Selection(price_id="p1", name="1", type_id="3744", price=2.0, suspended=False),
            Selection(price_id="p2", name="X", type_id="3745", price=3.3, suspended=False),
            Selection(price_id="p3", name="2", type_id="3746", price=4.0, suspended=False),
        ])],
    )
    await market_repo.insert_snapshot(MarketSnapshot(
        event_id=eid, phase=SnapshotPhase.PREMATCH_1,
        taken_at=base, markets=[market],
        total_market_count=1, total_selection_count=3, suspended_count=0,
    ))
    await market_repo.insert_snapshot(MarketSnapshot(
        event_id=eid, phase=SnapshotPhase.LIVE,
        taken_at=base, markets=[market],
        total_market_count=1, total_selection_count=3, suspended_count=0,
    ))
    await market_repo.insert_comparison(MarketComparison(
        event_id=eid,
        compared_at=base,
        prematch_phase=SnapshotPhase.PREMATCH_1,
        markets_added=0, markets_dropped=0, markets_kept=1,
        retention_pct=100.0, dropped_key_markets=[],
        max_odds_shift_pct=0.0, triggered_alert=False,
        details={"dropped": [], "added": [], "odds_shifts": []},
    ))
    return eid


class TestPerEventCsv:
    async def test_generates_per_event_csv(self, reporter, event_repo, market_repo):
        eid = await _seed(event_repo, market_repo)
        csv_text = await reporter.generate_per_event_csv(eid)

        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        assert len(rows) == 6
        first = rows[0]
        assert first["market_type_name"] == "1X2 - FT"
        assert first["phase"] in ("PREMATCH_1", "LIVE")


class TestAggregateCsv:
    async def test_generates_aggregate_csv(self, reporter, event_repo, market_repo):
        await _seed(event_repo, market_repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        csv_text = await reporter.generate_aggregate_csv(start, end)

        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["betpawa_event_id"] == "99001"
        assert "retention_pct" in rows[0]


class TestSlackSummary:
    async def test_generates_markets_summary_block(self, reporter, event_repo, market_repo):
        await _seed(event_repo, market_repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        block = await reporter.generate_markets_summary_block(start, end)
        assert "Markets" in block
        assert "1" in block


class TestMinimalSummary:
    async def test_generates_minimal_summary(self, reporter, event_repo, market_repo):
        await _seed(event_repo, market_repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        text = await reporter.generate_minimal_summary(start, end, retention_threshold=60.0)
        assert "Market Retention" in text
        assert "Events with market snapshots: 1" in text
        assert "Avg market retention:" in text

    async def test_minimal_summary_counts_significant_drops(self, reporter, event_repo, market_repo):
        """Events below retention threshold are counted as significant drops."""
        base = datetime(2026, 4, 14, 15, 0, tzinfo=UTC)
        event = TrackedEvent(
            betpawa_event_id="99002",
            home_team="Team C", away_team="Team D",
            competition="LaLiga", country="Spain",
            scheduled_kickoff=base, status=EventStatus.LIVE,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="67890")],
            first_seen_prematch=base, first_seen_live=base,
            transition_delay_sec=60,
        )
        eid = await event_repo.insert_event(event)
        await market_repo.insert_comparison(MarketComparison(
            event_id=eid,
            compared_at=base,
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0, markets_dropped=8, markets_kept=2,
            retention_pct=20.0, dropped_key_markets=["1X2 - FT"],
            max_odds_shift_pct=25.0, triggered_alert=True,
            details={"dropped": [], "added": [], "odds_shifts": []},
        ))

        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        text = await reporter.generate_minimal_summary(start, end, retention_threshold=60.0)
        assert "significant drops" in text.lower() or "Significant drops" in text
        assert "1" in text  # one event below threshold

    async def test_minimal_summary_empty_period(self, reporter, event_repo, market_repo):
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        text = await reporter.generate_minimal_summary(start, end, retention_threshold=60.0)
        assert "No market comparisons" in text


class TestTopLeaguesNoRetention:
    async def test_top_leagues_excludes_retention(self, reporter, event_repo, market_repo):
        """Top leagues block should NOT include market retention %."""
        base = datetime(2026, 4, 14, 15, 0, tzinfo=UTC)
        event = TrackedEvent(
            betpawa_event_id="99010",
            home_team="Team X", away_team="Team Y",
            competition="EPL", competition_id="11965",
            country="England",
            scheduled_kickoff=base, status=EventStatus.LIVE,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="111")],
            first_seen_prematch=base, first_seen_live=base,
            transition_delay_sec=60,
        )
        eid = await event_repo.insert_event(event)

        # Insert a comparison so retention data exists
        await market_repo.insert_comparison(MarketComparison(
            event_id=eid,
            compared_at=base,
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0, markets_dropped=2, markets_kept=8,
            retention_pct=80.0, dropped_key_markets=[],
            max_odds_shift_pct=0.0, triggered_alert=False,
            details={"dropped": [], "added": [], "odds_shifts": []},
        ))

        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        block = await reporter.generate_top_leagues_block(
            start, end,
            alert_competition_ids=["11965"],
            on_time_threshold_seconds=300,
        )
        assert "EPL" in block
        assert "retention" not in block.lower()
