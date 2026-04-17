"""Market comparator — diffs snapshots and decides whether to alert."""

from datetime import datetime

from live_coverage_bot.config.models import MarketsConfig
from live_coverage_bot.models.markets import (
    AddedMarketDetail,
    ComparisonDetails,
    DroppedMarketDetail,
    Market,
    MarketComparison,
    MarketSnapshot,
    OddsShiftDetail,
)


def compare_snapshots(
    prematch: MarketSnapshot,
    live: MarketSnapshot,
    config: MarketsConfig,
    now: datetime,
) -> MarketComparison:
    """Produce a MarketComparison by diffing a prematch snapshot against a live snapshot."""
    pre_by_id: dict[str, Market] = {m.market_type_id: m for m in prematch.markets}
    live_by_id: dict[str, Market] = {m.market_type_id: m for m in live.markets}

    kept_ids = set(pre_by_id) & set(live_by_id)
    dropped_ids = set(pre_by_id) - set(live_by_id)
    added_ids = set(live_by_id) - set(pre_by_id)

    total_prematch = len(pre_by_id)
    retention_pct = (len(kept_ids) / total_prematch * 100) if total_prematch else 0.0

    dropped_details = [
        DroppedMarketDetail(
            market_type_id=mt_id,
            market_type_name=pre_by_id[mt_id].market_type_name,
        )
        for mt_id in dropped_ids
    ]
    added_details = [
        AddedMarketDetail(
            market_type_id=mt_id,
            market_type_name=live_by_id[mt_id].market_type_name,
        )
        for mt_id in added_ids
    ]

    key_markets = set(config.key_markets)
    dropped_key_markets = [
        d.market_type_name for d in dropped_details if d.market_type_name in key_markets
    ]

    odds_shifts: list[OddsShiftDetail] = []
    max_shift = 0.0
    for mt_id in kept_ids:
        pre_m = pre_by_id[mt_id]
        live_m = live_by_id[mt_id]
        pre_rows_by_handicap = {(r.handicap or ""): r for r in pre_m.rows}
        for live_row in live_m.rows:
            pre_row = pre_rows_by_handicap.get(live_row.handicap or "")
            if pre_row is None:
                continue
            pre_sels_by_type = {s.type_id: s for s in pre_row.selections}
            for live_sel in live_row.selections:
                if live_sel.suspended:
                    continue
                pre_sel = pre_sels_by_type.get(live_sel.type_id)
                if pre_sel is None or pre_sel.suspended or pre_sel.price <= 0:
                    continue
                shift = abs(live_sel.price - pre_sel.price) / pre_sel.price * 100
                if shift > max_shift:
                    max_shift = shift
                odds_shifts.append(OddsShiftDetail(
                    market_type_name=pre_m.market_type_name,
                    selection_name=pre_sel.name,
                    selection_type_id=pre_sel.type_id,
                    handicap=pre_row.handicap,
                    prematch_price=pre_sel.price,
                    live_price=live_sel.price,
                    shift_pct=round(shift, 2),
                ))

    thresholds = config.alert_thresholds
    triggered = (
        retention_pct < thresholds.retention_below_pct
        or (thresholds.any_key_market_dropped and bool(dropped_key_markets))
        or max_shift >= thresholds.max_odds_shift_pct
    )

    return MarketComparison(
        event_id=prematch.event_id,
        compared_at=now,
        prematch_phase=prematch.phase,
        markets_added=len(added_ids),
        markets_dropped=len(dropped_ids),
        markets_kept=len(kept_ids),
        retention_pct=round(retention_pct, 2),
        dropped_key_markets=dropped_key_markets,
        max_odds_shift_pct=round(max_shift, 2),
        triggered_alert=triggered,
        details=ComparisonDetails(
            dropped=dropped_details,
            added=added_details,
            odds_shifts=odds_shifts,
        ),
    )
