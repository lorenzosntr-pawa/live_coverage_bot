"""Weekly report generator for prematch-to-live event tracking."""

import csv
import io
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta

from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent

logger = logging.getLogger(__name__)


class WeeklyReporter:
    """Generates weekly summary reports from tracked event data."""

    WEEKDAY_MAP = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }

    def __init__(self, repo: EventRepository, week_starts: str = "tuesday") -> None:
        self._repo = repo
        self._week_starts = week_starts.lower()

    def compute_report_period(self, report_time: datetime) -> tuple[datetime, datetime]:
        """Compute the Tue-Mon period ending before report_time."""
        target_weekday = self.WEEKDAY_MAP[self._week_starts]
        days_since_start = (report_time.weekday() - target_weekday) % 7
        period_end_date = (report_time - timedelta(days=days_since_start)).date()
        period_start_date = period_end_date - timedelta(days=6)
        start = datetime(
            period_start_date.year, period_start_date.month, period_start_date.day,
            tzinfo=UTC,
        )
        end = datetime(
            period_end_date.year, period_end_date.month, period_end_date.day,
            23, 59, 59, tzinfo=UTC,
        )
        return start, end

    async def generate_slack_summary(
        self, start: datetime, end: datetime
    ) -> str:
        """Generate the Slack-formatted weekly summary."""
        events = await self._repo.get_events_in_date_range(start, end)
        total = len(events)

        on_time = [e for e in events if e.status == EventStatus.LIVE and (e.transition_delay_sec or 0) < 300]
        late = [e for e in events if e.status == EventStatus.LIVE and (e.transition_delay_sec or 0) >= 300]
        never_live = [e for e in events if e.status == EventStatus.NEVER_LIVE]
        removed = [e for e in events if e.status == EventStatus.REMOVED]

        def pct(count: int) -> str:
            return f"{count / total * 100:.1f}%" if total > 0 else "0%"

        start_str = start.strftime("%b %d")
        end_str = end.strftime("%b %d, %Y")
        lines = [
            f"\U0001f4ca Weekly Report \u2014 {start_str}\u2013{end_str} (Tue\u2013Mon)",
            "",
            f"Total prematch events tracked: {total}",
            f"\u2705 Went live on time: {len(on_time)} ({pct(len(on_time))})",
            f"\U0001f7e1 Went live late: {len(late)} ({pct(len(late))})",
            f"\U0001f534 Never went live: {len(never_live)} ({pct(len(never_live))})",
            f"\U0001f5d1\ufe0f Removed before kickoff: {len(removed)} ({pct(len(removed))})",
        ]

        if late:
            delays = [e.transition_delay_sec for e in late if e.transition_delay_sec]
            if delays:
                avg_delay = sum(delays) / len(delays) / 60
                worst = max(delays) / 60
                worst_event = next(e for e in late if e.transition_delay_sec == max(delays))
                lines.append("")
                lines.append(f"Avg delay (late events): {avg_delay:.1f} min")
                lines.append(
                    f"Worst delay: {int(worst)} min \u2014 "
                    f"{worst_event.home_team} vs {worst_event.away_team} "
                    f"({worst_event.scheduled_kickoff.strftime('%b %d')})"
                )

        lines.append("")
        lines.append("By provider:")
        provider_stats = self._compute_provider_stats(events)
        for provider_type, stats in provider_stats.items():
            on_time_pct = f"{stats['on_time'] / stats['total'] * 100:.0f}%" if stats["total"] > 0 else "0%"
            avg_str = f"{stats['avg_delay']:.1f}min" if stats["avg_delay"] > 0 else "n/a"
            lines.append(
                f"  {provider_type}: {stats['total']} events, "
                f"{on_time_pct} on time, avg delay {avg_str}"
            )

        problem_events = late + never_live
        if problem_events:
            comp_counts: Counter[str] = Counter()
            for e in problem_events:
                comp_counts[e.competition] += 1

            lines.append("")
            lines.append("Top competitions with most issues:")
            for i, (comp, count) in enumerate(comp_counts.most_common(5), 1):
                comp_late = sum(1 for e in late if e.competition == comp)
                comp_never = sum(1 for e in never_live if e.competition == comp)
                parts = []
                if comp_late:
                    parts.append(f"{comp_late} late")
                if comp_never:
                    parts.append(f"{comp_never} never live")
                lines.append(f"  {i}. {comp} \u2014 {', '.join(parts)}")

        return "\n".join(lines)

    async def generate_csv(self, start: datetime, end: datetime) -> str:
        """Generate CSV content for the detailed weekly report."""
        events = await self._repo.get_events_in_date_range(start, end)

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "date", "betpawa_event_id", "home_team", "away_team",
                "competition", "country", "provider_type", "provider_id",
                "scheduled_kickoff", "status", "first_seen_live",
                "transition_delay_sec",
            ],
        )
        writer.writeheader()

        for event in events:
            primary_provider = event.provider_ids[0] if event.provider_ids else None
            writer.writerow({
                "date": event.scheduled_kickoff.strftime("%Y-%m-%d"),
                "betpawa_event_id": event.betpawa_event_id,
                "home_team": event.home_team,
                "away_team": event.away_team,
                "competition": event.competition,
                "country": event.country or "",
                "provider_type": primary_provider.type.value if primary_provider else "",
                "provider_id": primary_provider.id if primary_provider else "",
                "scheduled_kickoff": event.scheduled_kickoff.isoformat(),
                "status": event.status.value,
                "first_seen_live": event.first_seen_live.isoformat() if event.first_seen_live else "",
                "transition_delay_sec": event.transition_delay_sec if event.transition_delay_sec is not None else "",
            })

        return output.getvalue()

    def _compute_provider_stats(self, events: list[TrackedEvent]) -> dict[str, dict]:
        """Compute per-provider statistics."""
        stats: dict[str, dict] = {}
        for event in events:
            for pid in event.provider_ids:
                ptype = pid.type.value
                if ptype not in stats:
                    stats[ptype] = {"total": 0, "on_time": 0, "delays": []}
                stats[ptype]["total"] += 1
                if event.status == EventStatus.LIVE:
                    delay = event.transition_delay_sec or 0
                    if delay < 300:
                        stats[ptype]["on_time"] += 1
                    else:
                        stats[ptype]["delays"].append(delay)

        for ptype in stats:
            delays = stats[ptype]["delays"]
            stats[ptype]["avg_delay"] = (sum(delays) / len(delays) / 60) if delays else 0
            del stats[ptype]["delays"]

        return stats
