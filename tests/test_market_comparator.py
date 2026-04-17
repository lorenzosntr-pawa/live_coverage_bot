"""Tests for market comparator (diff + alert decision)."""

from datetime import UTC, datetime

from live_coverage_bot.config.models import (
    MarketAlertThresholdsConfig,
    MarketsConfig,
)
from live_coverage_bot.core.market_comparator import compare_snapshots
from live_coverage_bot.models.markets import (
    ComparisonDetails,
    DroppedMarketDetail,
    Market,
    MarketRow,
    MarketSnapshot,
    OddsShiftDetail,
    Selection,
    SnapshotPhase,
)


def _snap(event_id: int, phase: SnapshotPhase, markets: list[Market]) -> MarketSnapshot:
    return MarketSnapshot(
        event_id=event_id,
        phase=phase,
        taken_at=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
        markets=markets,
        total_market_count=len(markets),
        total_selection_count=sum(len(r.selections) for m in markets for r in m.rows),
        suspended_count=sum(
            1 for m in markets for r in m.rows for s in r.selections if s.suspended
        ),
    )


def _market_1x2(home_odds=2.0, draw_odds=3.3, away_odds=4.0):
    return Market(
        market_type_id="3743", market_type_name="1X2 - FT", priority=1,
        rows=[MarketRow(row_id="r1", handicap=None, selections=[
            Selection(price_id="p1", name="1", type_id="3744", price=home_odds, suspended=False),
            Selection(price_id="p2", name="X", type_id="3745", price=draw_odds, suspended=False),
            Selection(price_id="p3", name="2", type_id="3746", price=away_odds, suspended=False),
        ])],
    )


def _market_btts():
    return Market(
        market_type_id="3795", market_type_name="Both Teams To Score - FT", priority=10,
        rows=[MarketRow(row_id="r2", handicap=None, selections=[
            Selection(price_id="p4", name="Yes", type_id="3796", price=1.85, suspended=False),
            Selection(price_id="p5", name="No", type_id="3797", price=1.95, suspended=False),
        ])],
    )


def _market_ou(handicap="2.5", over=1.85, under=1.95):
    return Market(
        market_type_id="5000", market_type_name="Total Score Over/Under - FT", priority=5,
        rows=[MarketRow(row_id="r3", handicap=handicap, selections=[
            Selection(price_id=f"pou_{handicap}_o", name="Over", type_id="5001",
                     price=over, suspended=False),
            Selection(price_id=f"pou_{handicap}_u", name="Under", type_id="5002",
                     price=under, suspended=False),
        ])],
    )


def _default_config() -> MarketsConfig:
    return MarketsConfig(
        alert_thresholds=MarketAlertThresholdsConfig(
            retention_below_pct=50,
            any_key_market_dropped=True,
            max_odds_shift_pct=30,
        ),
        key_markets=[
            "1X2 - FT",
            "Total Score Over/Under - FT",
            "Both Teams To Score - FT",
        ],
    )


class TestCompareSnapshots:
    def test_full_retention_no_alert(self):
        markets = [_market_1x2()]
        pre = _snap(1, SnapshotPhase.PREMATCH_1, markets)
        live = _snap(1, SnapshotPhase.LIVE, markets)
        cfg = _default_config()

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert cmp.markets_dropped == 0
        assert cmp.markets_added == 0
        assert cmp.markets_kept == 1
        assert cmp.retention_pct == 100.0
        assert not cmp.triggered_alert

    def test_market_dropped(self):
        pre = _snap(1, SnapshotPhase.PREMATCH_1, [_market_1x2(), _market_btts()])
        live = _snap(1, SnapshotPhase.LIVE, [_market_1x2()])
        cfg = _default_config()

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert cmp.markets_dropped == 1
        assert cmp.markets_kept == 1
        assert cmp.retention_pct == 50.0
        assert "Both Teams To Score - FT" in cmp.dropped_key_markets
        assert cmp.triggered_alert

    def test_retention_below_threshold(self):
        pre_markets = [_market_1x2(), _market_btts(), _market_ou(), _market_ou("3.5")]
        live_markets = [_market_1x2()]
        pre = _snap(1, SnapshotPhase.PREMATCH_1, pre_markets)
        live = _snap(1, SnapshotPhase.LIVE, live_markets)
        cfg = MarketsConfig(
            alert_thresholds=MarketAlertThresholdsConfig(
                retention_below_pct=50,
                any_key_market_dropped=False,
                max_odds_shift_pct=1000,
            ),
            key_markets=[],
        )

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert cmp.retention_pct == 25.0
        assert cmp.triggered_alert

    def test_big_odds_shift(self):
        pre = _snap(1, SnapshotPhase.PREMATCH_1, [_market_1x2(home_odds=2.0)])
        live = _snap(1, SnapshotPhase.LIVE, [_market_1x2(home_odds=2.7)])
        cfg = MarketsConfig(
            alert_thresholds=MarketAlertThresholdsConfig(
                retention_below_pct=0,
                any_key_market_dropped=False,
                max_odds_shift_pct=30,
            ),
            key_markets=[],
        )

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert cmp.max_odds_shift_pct >= 30
        assert cmp.triggered_alert

    def test_row_matched_by_handicap(self):
        pre = _snap(1, SnapshotPhase.PREMATCH_1, [_market_ou("2.5"), _market_ou("3.5")])
        live = _snap(1, SnapshotPhase.LIVE, [_market_ou("2.5", over=1.9, under=1.9)])
        cfg = _default_config()

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert cmp.markets_kept == 1

    def test_suspended_selections_excluded_from_shift(self):
        pre = _snap(1, SnapshotPhase.PREMATCH_1, [_market_1x2(home_odds=2.0)])
        live_market = Market(
            market_type_id="3743", market_type_name="1X2 - FT", priority=1,
            rows=[MarketRow(row_id="r1", handicap=None, selections=[
                Selection(price_id="p1", name="1", type_id="3744", price=5.0, suspended=True),
                Selection(price_id="p2", name="X", type_id="3745", price=3.3, suspended=False),
                Selection(price_id="p3", name="2", type_id="3746", price=4.0, suspended=False),
            ])],
        )
        live = _snap(1, SnapshotPhase.LIVE, [live_market])
        cfg = _default_config()

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert cmp.max_odds_shift_pct == 0


class TestTypedDetails:
    def test_details_has_typed_dropped(self):
        pre = _snap(1, SnapshotPhase.PREMATCH_1, [_market_1x2(), _market_btts()])
        live = _snap(1, SnapshotPhase.LIVE, [_market_1x2()])
        cfg = _default_config()
        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert isinstance(cmp.details, ComparisonDetails)
        assert len(cmp.details.dropped) == 1
        assert isinstance(cmp.details.dropped[0], DroppedMarketDetail)
        assert cmp.details.dropped[0].market_type_name == "Both Teams To Score - FT"

    def test_details_has_typed_odds_shifts(self):
        pre = _snap(1, SnapshotPhase.PREMATCH_1, [_market_1x2(home_odds=2.0)])
        live = _snap(1, SnapshotPhase.LIVE, [_market_1x2(home_odds=2.5)])
        cfg = _default_config()
        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert len(cmp.details.odds_shifts) > 0
        shift = cmp.details.odds_shifts[0]
        assert isinstance(shift, OddsShiftDetail)
        assert shift.prematch_price == 2.0
        assert shift.live_price == 2.5
        assert shift.shift_pct > 0
