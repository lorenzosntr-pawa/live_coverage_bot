"""Market reporter — per-event CSV, aggregate CSV, and Slack summary block."""

import csv
import io
import logging
from collections import Counter
from datetime import datetime

from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.models.markets import MarketComparison

logger = logging.getLogger(__name__)


class MarketReporter:
    """Generates market-focused reports from snapshot + comparison data."""

    def __init__(
        self, event_repo: EventRepository, market_repo: MarketRepository
    ) -> None:
        self._events = event_repo
        self._markets = market_repo

    async def generate_per_event_csv(self, event_id: int) -> str:
        """Full market timeline for one event across all phases."""
        snapshots = await self._markets.get_snapshots_for_event(event_id)
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "phase", "taken_at", "market_type_id", "market_type_name",
                "handicap", "selection_name", "selection_type_id",
                "price", "suspended",
            ],
        )
        writer.writeheader()

        for snap in snapshots:
            for market in snap.markets:
                for row in market.rows:
                    for sel in row.selections:
                        writer.writerow({
                            "phase": snap.phase.value,
                            "taken_at": snap.taken_at.isoformat(),
                            "market_type_id": market.market_type_id,
                            "market_type_name": market.market_type_name,
                            "handicap": row.handicap or "",
                            "selection_name": sel.name,
                            "selection_type_id": sel.type_id,
                            "price": sel.price,
                            "suspended": sel.suspended,
                        })
        return output.getvalue()

    async def generate_aggregate_csv(self, start: datetime, end: datetime) -> str:
        """One row per event with a comparison in the date range."""
        comparisons = await self._markets.get_comparisons_in_date_range(start, end)

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "betpawa_event_id", "home", "away", "competition", "kickoff",
                "prematch_markets", "live_markets", "retention_pct",
                "dropped_key_markets", "max_odds_shift_pct", "triggered_alert",
            ],
        )
        writer.writeheader()

        for cmp in comparisons:
            event = await self._events.get_by_id(cmp.event_id)
            if event is None:
                continue
            writer.writerow({
                "betpawa_event_id": event.betpawa_event_id,
                "home": event.home_team,
                "away": event.away_team,
                "competition": event.competition,
                "kickoff": event.scheduled_kickoff.isoformat(),
                "prematch_markets": cmp.markets_dropped + cmp.markets_kept,
                "live_markets": cmp.markets_kept + cmp.markets_added,
                "retention_pct": cmp.retention_pct,
                "dropped_key_markets": ";".join(cmp.dropped_key_markets),
                "max_odds_shift_pct": cmp.max_odds_shift_pct,
                "triggered_alert": cmp.triggered_alert,
            })
        return output.getvalue()

    async def generate_minimal_summary(
        self, start: datetime, end: datetime, retention_threshold: float
    ) -> str:
        """Return a minimal market retention summary for the weekly Slack message."""
        all_comparisons = await self._markets.get_comparisons_in_date_range(start, end)

        # Deduplicate: one comparison per event, prefer LIVE_5 > LIVE_2 > LIVE_0
        seen_events: set[int] = set()
        comparisons: list[MarketComparison] = []
        for cmp in all_comparisons:
            if cmp.event_id in seen_events:
                continue
            best = await self._markets.get_best_comparison_for_event(cmp.event_id)
            if best:
                comparisons.append(best)
                seen_events.add(cmp.event_id)

        if not comparisons:
            return "\U0001f4ca Market Retention Report\n\nNo market comparisons in this period."

        avg_retention = sum(c.retention_pct for c in comparisons) / len(comparisons)
        significant_drops = sum(
            1 for c in comparisons if c.retention_pct < retention_threshold
        )

        return "\n".join([
            "\U0001f4ca Market Retention Report",
            "",
            f"Events with market snapshots: {len(comparisons)}",
            f"Avg market retention: {avg_retention:.0f}%",
            f"Significant drops (retention <{retention_threshold:.0f}%): {significant_drops}",
        ])

    async def generate_markets_summary_block(
        self, start: datetime, end: datetime
    ) -> str:
        """Return the markets section for the weekly Slack summary."""
        all_comparisons = await self._markets.get_comparisons_in_date_range(start, end)
        # Deduplicate: one comparison per event, prefer LIVE_5 > LIVE_2 > LIVE_0
        seen_events: set[int] = set()
        comparisons: list[MarketComparison] = []
        for cmp in all_comparisons:
            if cmp.event_id in seen_events:
                continue
            best = await self._markets.get_best_comparison_for_event(cmp.event_id)
            if best:
                comparisons.append(best)
                seen_events.add(cmp.event_id)

        total = len(comparisons)

        if total == 0:
            return "\u2500\u2500\u2500 Markets \u2500\u2500\u2500\n\nNo market comparisons in this period."

        avg_prematch = sum(c.markets_dropped + c.markets_kept for c in comparisons) / total
        avg_live = sum(c.markets_kept + c.markets_added for c in comparisons) / total
        avg_retention = sum(c.retention_pct for c in comparisons) / total
        ge_50_dropped = sum(1 for c in comparisons if c.retention_pct <= 50)
        ge_80_dropped = sum(1 for c in comparisons if c.retention_pct <= 20)

        dropped_key_market_counts: Counter[str] = Counter()
        for c in comparisons:
            for name in c.dropped_key_markets:
                dropped_key_market_counts[name] += 1

        drop_count: Counter[str] = Counter()
        for c in comparisons:
            for d in c.details.dropped:
                drop_count[d.market_type_name] += 1

        lines = [
            "\u2500\u2500\u2500 Markets \u2500\u2500\u2500",
            "",
            f"Events with market snapshots: {total}",
            f"Avg markets in prematch: {avg_prematch:.1f}",
            f"Avg markets in live: {avg_live:.1f}",
            f"Avg market retention: {avg_retention:.0f}%",
            f"Events where \u226550% of markets dropped: {ge_50_dropped}",
            f"Events where \u226580% of markets dropped: {ge_80_dropped}",
        ]

        if dropped_key_market_counts:
            lines.append("")
            lines.append("Key markets that disappeared when going live:")
            for name, count in dropped_key_market_counts.most_common(5):
                lines.append(f"  {name}: {count} events")

        if drop_count:
            lines.append("")
            lines.append("Most frequently dropped markets:")
            for name, count in drop_count.most_common(5):
                lines.append(f"  {name}: dropped in {count} events")

        return "\n".join(lines)

    async def generate_top_leagues_block(
        self,
        start: datetime,
        end: datetime,
        alert_competition_ids: list[str],
        on_time_threshold_seconds: int = 300,
    ) -> str:
        """Return a per-league breakdown for top leagues in the weekly report."""
        if not alert_competition_ids:
            return ""

        all_events = await self._events.get_events_in_date_range(start, end)
        top_ids = set(alert_competition_ids)
        top_events = [e for e in all_events if e.competition_id in top_ids]

        if not top_events:
            return "\u2500\u2500\u2500 Top Leagues \u2500\u2500\u2500\n\nNo events for top leagues in this period."

        # Group events by (competition, country) for display
        leagues: dict[tuple[str, str], list[TrackedEvent]] = {}
        for e in top_events:
            key = (e.competition, e.country or "")
            leagues.setdefault(key, []).append(e)

        # Build per-league market retention map: event_id → best retention_pct
        retention_map: dict[int, float] = {}
        for e in top_events:
            if e.id is None:
                continue
            best = await self._markets.get_best_comparison_for_event(e.id)
            if best:
                retention_map[e.id] = best.retention_pct

        lines = ["\u2500\u2500\u2500 Top Leagues \u2500\u2500\u2500", ""]

        # Sort leagues by total events descending
        for (comp, country), events in sorted(
            leagues.items(), key=lambda x: len(x[1]), reverse=True
        ):
            total = len(events)
            monitored = [e for e in events if e.status != EventStatus.UNMONITORED]
            on_time = sum(
                1 for e in monitored
                if e.status == EventStatus.LIVE
                and (e.transition_delay_sec or 0) < on_time_threshold_seconds
            )
            late = sum(
                1 for e in monitored
                if e.status == EventStatus.LIVE
                and (e.transition_delay_sec or 0) >= on_time_threshold_seconds
            )
            never_live = sum(1 for e in monitored if e.status == EventStatus.NEVER_LIVE)

            delays = [
                e.transition_delay_sec
                for e in monitored
                if e.status == EventStatus.LIVE
                and (e.transition_delay_sec or 0) >= on_time_threshold_seconds
                and e.transition_delay_sec is not None
            ]
            avg_delay_str = f"{sum(delays) / len(delays) / 60:.1f}min" if delays else "n/a"

            retentions = [
                retention_map[e.id]
                for e in events
                if e.id is not None and e.id in retention_map
            ]
            retention_str = f"{sum(retentions) / len(retentions):.0f}%" if retentions else "n/a"

            on_time_pct = f"{on_time / len(monitored) * 100:.0f}%" if monitored else "0%"
            label = f"{comp} ({country})" if country else comp

            lines.append(
                f"  {label}: {total} events | {on_time_pct} on time | "
                f"{late} late, {never_live} never live | "
                f"avg delay {avg_delay_str} | {retention_str} retention"
            )

        return "\n".join(lines)
