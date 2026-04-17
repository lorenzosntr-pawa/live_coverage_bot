"""Tests for market domain models."""

from datetime import UTC, datetime

from live_coverage_bot.models.markets import (
    ComparisonDetails,
    DroppedMarketDetail,
    Market,
    MarketComparison,
    MarketRow,
    MarketSnapshot,
    MatchState,
    Selection,
    SnapshotPhase,
)


class TestSnapshotPhase:
    def test_phases_exist(self):
        assert SnapshotPhase.PREMATCH_60 == "PREMATCH_60"
        assert SnapshotPhase.PREMATCH_15 == "PREMATCH_15"
        assert SnapshotPhase.PREMATCH_1 == "PREMATCH_1"
        assert SnapshotPhase.LIVE == "LIVE"

    def test_is_prematch(self):
        assert SnapshotPhase.PREMATCH_60.is_prematch
        assert SnapshotPhase.PREMATCH_15.is_prematch
        assert SnapshotPhase.PREMATCH_1.is_prematch
        assert not SnapshotPhase.LIVE.is_prematch


class TestMarketModels:
    def test_selection_model(self):
        s = Selection(
            price_id="1430463477", name="1", type_id="3744",
            price=1.94, suspended=False,
        )
        assert s.price == 1.94
        assert not s.suspended

    def test_market_row_with_handicap(self):
        row = MarketRow(
            row_id="368398521", handicap="2.5",
            selections=[
                Selection(price_id="x", name="Over", type_id="5001", price=1.85, suspended=False),
                Selection(price_id="y", name="Under", type_id="5002", price=1.95, suspended=False),
            ],
        )
        assert row.handicap == "2.5"
        assert len(row.selections) == 2

    def test_market_model(self):
        m = Market(
            market_type_id="3743", market_type_name="1X2 - FT",
            priority=1, rows=[],
        )
        assert m.market_type_id == "3743"
        assert m.priority == 1


class TestMarketSnapshot:
    def test_snapshot_fields(self):
        snap = MarketSnapshot(
            event_id=5,
            phase=SnapshotPhase.PREMATCH_1,
            taken_at=datetime(2026, 4, 15, 14, 59, tzinfo=UTC),
            markets=[],
            total_market_count=87,
            total_selection_count=312,
            suspended_count=4,
        )
        assert snap.event_id == 5
        assert snap.phase == SnapshotPhase.PREMATCH_1
        assert snap.total_market_count == 87

    def test_markets_json_serialization(self):
        snap = MarketSnapshot(
            event_id=5,
            phase=SnapshotPhase.LIVE,
            taken_at=datetime(2026, 4, 15, 15, 1, tzinfo=UTC),
            markets=[
                Market(
                    market_type_id="3743", market_type_name="1X2 - FT", priority=1,
                    rows=[MarketRow(
                        row_id="r1", handicap=None,
                        selections=[Selection(price_id="p1", name="1", type_id="3744",
                                              price=2.10, suspended=False)],
                    )],
                )
            ],
            total_market_count=1,
            total_selection_count=1,
            suspended_count=0,
        )
        json_str = snap.markets_json
        assert '"market_type_id":"3743"' in json_str or '"market_type_id": "3743"' in json_str
        restored = MarketSnapshot.markets_from_json(json_str)
        assert len(restored) == 1
        assert restored[0].market_type_id == "3743"


class TestMarketComparison:
    def test_comparison_fields(self):
        c = MarketComparison(
            event_id=5,
            compared_at=datetime(2026, 4, 15, 15, 1, tzinfo=UTC),
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0,
            markets_dropped=53,
            markets_kept=34,
            retention_pct=39.1,
            dropped_key_markets=["Both Teams To Score - FT"],
            max_odds_shift_pct=16.7,
            triggered_alert=True,
            details=ComparisonDetails(
                dropped=[DroppedMarketDetail(market_type_id="m1", market_type_name="Corner markets")],
                added=[],
                odds_shifts=[],
            ),
        )
        assert c.retention_pct == 39.1
        assert c.triggered_alert
        assert "Both Teams To Score - FT" in c.dropped_key_markets


class TestMatchState:
    def test_format_display(self):
        ms = MatchState(minute="33", period="First Half", home_score=1, away_score=0)
        assert ms.display == "\u23f1 33' | First Half | 1-0"

    def test_format_display_no_data(self):
        ms = MatchState()
        assert ms.display == "\u23f1 ?' | ? | ?-?"

    def test_json_round_trip(self):
        ms = MatchState(minute="10", period="First Half", home_score=0, away_score=0)
        data = ms.model_dump()
        restored = MatchState(**data)
        assert restored == ms


class TestNewSnapshotPhases:
    def test_pre_removal_is_prematch(self):
        assert SnapshotPhase.PRE_REMOVAL.is_prematch

    def test_live_0_is_not_prematch(self):
        assert not SnapshotPhase.LIVE_0.is_prematch

    def test_live_phases(self):
        assert SnapshotPhase.LIVE_0.is_live
        assert SnapshotPhase.LIVE_2.is_live
        assert SnapshotPhase.LIVE_5.is_live
        assert not SnapshotPhase.PREMATCH_60.is_live

    def test_legacy_live_is_live(self):
        assert SnapshotPhase.LIVE.is_live
