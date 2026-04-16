"""Monitoring loop orchestration for prematch-to-live event tracking."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from live_coverage_bot.clients.betpawa import BetPawaClient, BetPawaError
from live_coverage_bot.clients.slack import SlackClient, SlackError
from live_coverage_bot.config.models import Settings
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.core.tracker import EventLifecycleTracker
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus

logger = logging.getLogger(__name__)


class MonitoringLoop:
    """Orchestrates prematch-to-live event monitoring."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._db: Database | None = None
        self._repo: EventRepository | None = None
        self._tracker: EventLifecycleTracker | None = None
        self._prematch_cycle_counter = 0
        self._last_report_date: datetime | None = None

    async def run(self) -> None:
        """Run the monitoring loop until interrupted."""
        db_path = self._settings.database.path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = Database(db_path)
        await self._db.initialize()
        self._repo = EventRepository(self._db)
        self._tracker = EventLifecycleTracker(
            self._repo,
            grace_period_minutes=self._settings.thresholds.grace_period_minutes,
            hard_timeout_minutes=self._settings.thresholds.hard_timeout_minutes,
        )

        async with (
            BetPawaClient(self._settings.betpawa) as betpawa,
            SlackClient(self._settings.slack) as slack,
        ):
            logger.info(
                "Monitoring loop started (live: %ds, prematch: %ds)",
                self._settings.polling.live_interval_seconds,
                self._settings.polling.prematch_interval_seconds,
            )

            while True:
                try:
                    now = datetime.now(tz=UTC)
                    await self._poll_cycle(betpawa, slack, now=now)
                    await self._check_weekly_report(slack, now)
                    await self._cleanup_old_events(now)
                except Exception as e:
                    logger.exception("Unexpected error in poll cycle: %s", e)

                await asyncio.sleep(self._settings.polling.live_interval_seconds)

        await self._db.close()

    async def _poll_cycle(
        self,
        betpawa: BetPawaClient,
        slack: SlackClient,
        now: datetime,
        force_prematch: bool = False,
    ) -> None:
        """Execute a single poll cycle."""
        assert self._tracker is not None
        assert self._repo is not None

        prematch_ratio = (
            self._settings.polling.prematch_interval_seconds
            // self._settings.polling.live_interval_seconds
        )
        is_first_cycle = self._prematch_cycle_counter == 0
        self._prematch_cycle_counter += 1
        should_fetch_prematch = (
            force_prematch
            or is_first_cycle
            or self._prematch_cycle_counter >= prematch_ratio
        )

        try:
            live_events = await betpawa.get_live_events()
        except BetPawaError as e:
            logger.warning("BetPawa live fetch failed, skipping cycle: %s", e)
            return

        live_betpawa_ids = BetPawaClient.build_live_betpawa_id_set(live_events)

        current_prematch_ids: set[str] | None = None
        if should_fetch_prematch:
            self._prematch_cycle_counter = 0
            try:
                upcoming = await betpawa.get_upcoming_events(
                    hours_ahead=self._settings.polling.prematch_lookahead_hours
                )
                feed = BetPawaClient.upcoming_to_tracker_feed(upcoming)
                new_ids = await self._tracker.register_prematch_events(feed, now=now)
                if new_ids:
                    logger.info("Registered %d new prematch events", len(new_ids))

                current_prematch_ids = {e.event_id for e in upcoming}

            except BetPawaError as e:
                logger.warning("BetPawa prematch fetch failed: %s", e)

        # Transitions first — this moves PREMATCH → LIVE for events that appeared in the
        # live feed, before detect_removed runs. Otherwise events going live (and thus
        # disappearing from the prematch feed) would be wrongly marked as REMOVED.
        transitions = await self._tracker.check_transitions(live_betpawa_ids, now=now)

        for t in transitions:
            await self._handle_transition(slack, t, now)

        if current_prematch_ids is not None:
            removed = await self._tracker.detect_removed(current_prematch_ids, now=now)
            if removed:
                logger.info("Detected %d removed prematch events", len(removed))

        active = await self._repo.get_active_events()
        prematch_count = sum(1 for e in active if e.status == EventStatus.PREMATCH)
        late_count = sum(1 for e in active if e.status == EventStatus.LATE)
        logger.info(
            "Cycle: %d live events, %d prematch tracked, %d late | %d transitions",
            len(live_events), prematch_count, late_count, len(transitions),
        )

    async def _handle_transition(
        self, slack: SlackClient, transition: dict, now: datetime
    ) -> None:
        """Send Slack alerts/updates for a state transition."""
        assert self._repo is not None
        event = transition["event"]
        old_status = transition["old_status"]
        new_status = transition["new_status"]

        refreshed = await self._repo.get_by_betpawa_id(event.betpawa_event_id)
        if refreshed is None:
            return

        try:
            if old_status == EventStatus.PREMATCH and new_status == EventStatus.LATE:
                ts = await slack.post_alert(refreshed, now)
                assert refreshed.id is not None
                await self._repo.update_slack_ts(refreshed.id, ts)
                logger.info(
                    "Alert sent: %s %s vs %s (LATE)",
                    refreshed.betpawa_event_id, refreshed.home_team, refreshed.away_team,
                )

            elif new_status in (EventStatus.LIVE, EventStatus.NEVER_LIVE):
                if refreshed.slack_message_ts:
                    await slack.update_message(refreshed.slack_message_ts, refreshed, now)
                    delay_sec = transition.get("delay_sec")
                    if new_status == EventStatus.LIVE and delay_sec is not None:
                        detail = f"delay: {delay_sec // 60}min"
                    elif new_status == EventStatus.NEVER_LIVE:
                        detail = "timed out"
                    else:
                        detail = None
                    reply_text = slack.format_thread_reply(
                        old_status, new_status, now, detail
                    )
                    await slack.post_thread_reply(refreshed.slack_message_ts, reply_text)
                    logger.info(
                        "Updated alert: %s %s vs %s (%s -> %s)",
                        refreshed.betpawa_event_id, refreshed.home_team,
                        refreshed.away_team, old_status, new_status,
                    )

        except SlackError as e:
            logger.warning("Slack operation failed for %s: %s", refreshed.betpawa_event_id, e)

    async def _check_weekly_report(self, slack: SlackClient, now: datetime) -> None:
        """Check if it's time to generate the weekly report."""
        assert self._repo is not None
        cfg = self._settings.reporting
        target_weekday = WeeklyReporter.WEEKDAY_MAP.get(cfg.day.lower())
        if target_weekday is None:
            return

        if now.weekday() != target_weekday:
            return

        hour, minute = map(int, cfg.time.split(":"))
        if now.hour != hour or now.minute < minute:
            return

        if self._last_report_date and self._last_report_date.date() == now.date():
            return

        reporter = WeeklyReporter(self._repo, week_starts=cfg.week_starts)
        start, end = reporter.compute_report_period(now)

        try:
            summary = await reporter.generate_slack_summary(start, end)
            await slack.post_summary(summary)

            csv_content = await reporter.generate_csv(start, end)
            output_dir = Path(cfg.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            csv_path = output_dir / f"weekly-{now.strftime('%Y-%m-%d')}.csv"
            csv_path.write_text(csv_content, encoding="utf-8")

            self._last_report_date = now
            logger.info("Weekly report generated: %s", csv_path)

        except Exception as e:
            logger.error("Failed to generate weekly report: %s", e)

    async def _cleanup_old_events(self, now: datetime) -> None:
        """Delete events older than retention period."""
        assert self._repo is not None
        retention_days = self._settings.database.retention_days
        cutoff = now - timedelta(days=retention_days)
        deleted = await self._repo.delete_events_before(cutoff)
        if deleted > 0:
            logger.info("Cleaned up %d old events (older than %d days)", deleted, retention_days)
