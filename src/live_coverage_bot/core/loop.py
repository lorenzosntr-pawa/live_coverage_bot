"""Monitoring loop orchestration for prematch-to-live event tracking."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from live_coverage_bot.clients.betpawa import BetPawaClient, BetPawaError
from live_coverage_bot.clients.slack import SlackClient, SlackError
from live_coverage_bot.config.models import Settings
from live_coverage_bot.core.market_comparator import compare_snapshots
from live_coverage_bot.core.market_reporter import MarketReporter
from live_coverage_bot.core.market_snapshotter import MarketSnapshotter
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.core.tracker import EventLifecycleTracker
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TransitionResult
from live_coverage_bot.models.markets import SnapshotPhase

logger = logging.getLogger(__name__)


class MonitoringLoop:
    """Orchestrates prematch-to-live event monitoring."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._db: Database | None = None
        self._repo: EventRepository | None = None
        self._market_repo: MarketRepository | None = None
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
        self._market_repo = MarketRepository(self._db)
        self._tracker = EventLifecycleTracker(
            self._repo,
            grace_period_minutes=self._settings.thresholds.grace_period_minutes,
            hard_timeout_minutes=self._settings.thresholds.hard_timeout_minutes,
        )

        async with (
            BetPawaClient(self._settings.betpawa) as betpawa,
            SlackClient(self._settings.slack) as slack,
        ):
            # Startup: detect downtime and recover
            now = datetime.now(tz=UTC)
            await self._startup_recovery(slack, now)

            logger.info(
                "Monitoring loop started (live: %ds, prematch: %ds)",
                self._settings.polling.live_interval_seconds,
                self._settings.polling.prematch_interval_seconds,
            )

            try:
                while True:
                    try:
                        now = datetime.now(tz=UTC)
                        await self._poll_cycle(betpawa, slack, now=now)
                        await self._check_weekly_report(slack, now)
                        await self._cleanup_old_events(now)
                    except (BetPawaError, SlackError) as e:
                        logger.warning("Error in poll cycle: %s", e)
                    except Exception:
                        logger.exception("Unexpected error in poll cycle")

                    await asyncio.sleep(self._settings.polling.live_interval_seconds)
            except Exception as e:
                # Crash notification — unhandled error that broke out of the loop
                logger.critical("Fatal error in monitoring loop: %s", e)
                crash_msg = slack.format_crash_message(
                    type(e).__name__, str(e)
                )
                await slack.post_bot_status(crash_msg)
                raise

        await self._db.close()

    async def _startup_recovery(self, slack: SlackClient, now: datetime) -> None:
        """Detect downtime from heartbeat gap and mark stale events."""
        assert self._repo is not None
        assert self._tracker is not None

        last_heartbeat = await self._repo.get_last_heartbeat()

        if last_heartbeat is not None:
            gap_seconds = (now - last_heartbeat).total_seconds()
            threshold = self._settings.polling.live_interval_seconds * 2

            if gap_seconds > threshold:
                logger.warning(
                    "Downtime detected: last heartbeat %s (%.0fs ago)",
                    last_heartbeat.strftime("%Y-%m-%d %H:%M UTC"),
                    gap_seconds,
                )

                unmonitored_count = await self._tracker.mark_unmonitored(
                    downtime_start=last_heartbeat, now=now
                )

                active_remaining = len(await self._repo.get_active_events())

                recovery_msg = slack.format_recovery_summary(
                    downtime_start=last_heartbeat,
                    now=now,
                    unmonitored_count=unmonitored_count,
                    active_remaining=active_remaining,
                )
                await slack.post_bot_status(recovery_msg)
            else:
                logger.info("Clean restart (last heartbeat %.0fs ago)", gap_seconds)
        else:
            # No heartbeat — either truly first run, or migrating from
            # pre-heartbeat code. Check for stale active events that prove
            # a previous session existed.
            stale_count = await self._tracker.mark_unmonitored(
                downtime_start=now, now=now
            )
            if stale_count > 0:
                active_remaining = len(await self._repo.get_active_events())
                logger.warning(
                    "No heartbeat found but %d stale events detected — "
                    "marking as UNMONITORED (pre-heartbeat migration)",
                    stale_count,
                )
                recovery_msg = slack.format_recovery_summary(
                    downtime_start=now,
                    now=now,
                    unmonitored_count=stale_count,
                    active_remaining=active_remaining,
                )
                await slack.post_bot_status(recovery_msg)
            else:
                logger.info("First run — no previous heartbeat found")

        # Record session start
        await self._repo.upsert_heartbeat(now, started_at=now)

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
        assert self._market_repo is not None

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

        # Lifecycle transitions first — so events going LIVE are not falsely marked REMOVED
        transitions = await self._tracker.check_transitions(live_betpawa_ids, now=now)
        live_transition_bp_ids: set[str] = {
            t.event.betpawa_event_id
            for t in transitions
            if t.new_status == EventStatus.LIVE
        }

        for t in transitions:
            await self._handle_transition(slack, t, now)

        # Market snapshots (prematch windows + live on transition)
        snapshotter = MarketSnapshotter(
            self._repo, self._market_repo, betpawa, self._settings.markets
        )

        if current_prematch_ids is not None:
            removed = await self._tracker.detect_removed(current_prematch_ids, now=now)
            if removed:
                logger.info("Detected %d removed prematch events", len(removed))

            for r in removed:
                if r.event.id is None:
                    continue
                taken_phases = await self._market_repo.phases_taken_for_event(r.event.id)
                if any(p.is_prematch for p in taken_phases):
                    # Use existing prematch snapshot market count
                    latest_pre = await self._market_repo.get_latest_prematch_snapshot(r.event.id)
                    if latest_pre:
                        await self._repo.update_removed_fields(
                            r.event.id, removed_at=now, market_count=latest_pre.total_market_count
                        )
                else:
                    # Take PRE_REMOVAL snapshot
                    result = await snapshotter._take_snapshot(r.event, SnapshotPhase.PRE_REMOVAL, now)
                    if result:
                        snaps = await self._market_repo.get_snapshots_for_event(r.event.id)
                        pre_removal = next(
                            (s for s in snaps if s.phase == SnapshotPhase.PRE_REMOVAL), None
                        )
                        if pre_removal:
                            await self._repo.update_removed_fields(
                                r.event.id, removed_at=now, market_count=pre_removal.total_market_count
                            )

            # Check if any REMOVED events reappeared in prematch
            prematch_recoveries = await self._tracker.detect_prematch_recovery(
                current_prematch_ids, now=now
            )
            for recovery in prematch_recoveries:
                await self._handle_transition(slack, recovery, now)
        taken = await snapshotter.run_cycle(
            now=now,
            live_transition_event_ids=live_transition_bp_ids,
        )

        # For each live snapshot taken this cycle, run comparison + alert routing
        for event_id, phase in taken:
            if not phase.is_live:
                continue
            await self._handle_market_comparison(slack, event_id, phase, now)

        # Cycle summary
        active = await self._repo.get_active_events()
        prematch_count = sum(1 for e in active if e.status == EventStatus.PREMATCH)
        late_count = sum(1 for e in active if e.status == EventStatus.LATE)
        logger.info(
            "Cycle: %d live events, %d prematch tracked, %d late | %d transitions",
            len(live_events), prematch_count, late_count, len(transitions),
        )

        # Update heartbeat
        assert self._repo is not None
        await self._repo.upsert_heartbeat(now)

    async def _handle_transition(
        self, slack: SlackClient, transition: TransitionResult, now: datetime
    ) -> None:
        """Send Slack alerts/updates for a state transition."""
        assert self._repo is not None
        event = transition.event
        old_status = transition.old_status
        new_status = transition.new_status

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
                    delay_sec = transition.delay_sec
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

            elif new_status == EventStatus.REMOVED:
                # Post removal alert
                refreshed.status = EventStatus.REMOVED  # Ensure status is set for formatting
                ts = await slack.post_alert(refreshed, now)
                assert refreshed.id is not None
                await self._repo.update_slack_ts(refreshed.id, ts)
                logger.info(
                    "Alert sent: %s %s vs %s (REMOVED)",
                    refreshed.betpawa_event_id, refreshed.home_team, refreshed.away_team,
                )

            elif old_status == EventStatus.REMOVED and new_status in (EventStatus.PREMATCH, EventStatus.LIVE):
                if refreshed.slack_message_ts:
                    feed = "prematch" if new_status == EventStatus.PREMATCH else "live"
                    gap_min = 0
                    if refreshed.removed_at:
                        gap_min = int((now - refreshed.removed_at).total_seconds() / 60)

                    current_markets = None
                    try:
                        if self._market_repo and refreshed.id:
                            snaps = await self._market_repo.get_snapshots_for_event(refreshed.id)
                            if snaps:
                                current_markets = snaps[-1].total_market_count
                    except Exception:
                        pass

                    reply = slack.format_recovery_reply(
                        feed=feed,
                        gap_minutes=gap_min,
                        pre_removal_markets=refreshed.pre_removal_market_count,
                        current_markets=current_markets,
                    )
                    await slack.post_thread_reply(refreshed.slack_message_ts, reply)

        except SlackError as e:
            logger.warning("Slack operation failed for %s: %s", refreshed.betpawa_event_id, e)

    async def _handle_market_comparison(
        self, slack: SlackClient, event_id: int, phase: SnapshotPhase, now: datetime
    ) -> None:
        """Run comparison for a live snapshot; route to Slack with detailed formatting."""
        assert self._repo is not None
        assert self._market_repo is not None

        prematch_snap = await self._market_repo.get_latest_prematch_snapshot(event_id)
        if prematch_snap is None:
            logger.info("No prematch snapshot for event %d — skipping comparison", event_id)
            return

        all_snaps = await self._market_repo.get_snapshots_for_event(event_id)
        live_snap = next((s for s in reversed(all_snaps) if s.phase == phase), None)
        if live_snap is None:
            return

        cmp = compare_snapshots(prematch_snap, live_snap, self._settings.markets, now=now)
        cmp.snapshot_phase = phase

        try:
            await self._market_repo.insert_comparison(cmp)
        except Exception as e:
            logger.warning("Market comparison insert failed for event %d: %s", event_id, e)
            return

        event = await self._repo.get_by_id(event_id)
        if event is None:
            return

        try:
            if phase == SnapshotPhase.LIVE_0:
                await self._handle_initial_comparison(slack, event, cmp, live_snap, now)
            elif phase in (SnapshotPhase.LIVE_2, SnapshotPhase.LIVE_5):
                await self._handle_followup_comparison(slack, event, cmp, live_snap, phase, now)
        except SlackError as e:
            logger.warning("Slack market alert failed for %s: %s", event.betpawa_event_id, e)

    async def _handle_initial_comparison(
        self, slack: SlackClient, event, cmp, live_snap, now: datetime
    ) -> None:
        """Handle the LIVE_0 comparison — post initial alert if triggered."""
        if not cmp.triggered_alert:
            return

        config = self._settings.markets

        if event.slack_message_ts:
            # Post detailed thread replies on existing alert thread
            missing_text = slack.format_missing_markets(cmp.details.dropped, config.key_markets)
            shifts_text = slack.format_odds_shifts(
                cmp.details.odds_shifts, config.odds_shift_display_threshold
            )
            match_header = live_snap.match_state.display if live_snap.match_state else ""
            thread_text = "\n".join(filter(None, [match_header, missing_text, shifts_text]))
            await slack.post_thread_reply(event.slack_message_ts, thread_text)
        else:
            # Post new standalone anomaly alert + detail thread
            parent_text = slack.format_market_anomaly_parent(event, cmp)
            ts = await slack.post_market_anomaly_alert(event, parent_text)
            assert event.id is not None
            await self._repo.update_slack_ts(event.id, ts)

            missing_text = slack.format_missing_markets(cmp.details.dropped, config.key_markets)
            shifts_text = slack.format_odds_shifts(
                cmp.details.odds_shifts, config.odds_shift_display_threshold
            )
            match_header = live_snap.match_state.display if live_snap.match_state else ""
            thread_text = "\n".join(filter(None, [match_header, missing_text, shifts_text]))
            await slack.post_thread_reply(ts, thread_text)

        logger.info("Posted market anomaly alert for %s", event.betpawa_event_id)

    async def _handle_followup_comparison(
        self, slack: SlackClient, event, cmp, live_snap, phase, now: datetime
    ) -> None:
        """Handle LIVE_2/LIVE_5 comparison — post update thread if event has an alert."""
        assert self._market_repo is not None

        # Find the previous comparison to compute delta
        all_cmps = await self._market_repo.get_comparisons_for_event(event.id)
        prev_cmp = None
        for c in reversed(all_cmps):
            if c.snapshot_phase != phase and c.snapshot_phase is not None:
                prev_cmp = c
                break

        if event.slack_message_ts is None:
            # No existing thread — fire new alert if this snapshot triggers
            if cmp.triggered_alert:
                parent_text = slack.format_market_anomaly_parent(event, cmp)
                ts = await slack.post_market_anomaly_alert(event, parent_text)
                assert event.id is not None
                await self._repo.update_slack_ts(event.id, ts)
            return

        # Post update in existing thread
        prev_kept = prev_cmp.markets_kept if prev_cmp else 0
        prev_retention = prev_cmp.retention_pct if prev_cmp else 0.0

        prev_dropped_names = (
            {d.market_type_name for d in prev_cmp.details.dropped}
            if prev_cmp else set()
        )
        curr_dropped_names = {d.market_type_name for d in cmp.details.dropped}

        recovered = sorted(prev_dropped_names - curr_dropped_names)
        still_missing = sorted(curr_dropped_names)

        offset_min = {SnapshotPhase.LIVE_2: 2, SnapshotPhase.LIVE_5: 5}

        update_text = slack.format_snapshot_update(
            match_state=live_snap.match_state,
            phase_label=f"+{offset_min.get(phase, '?')}min",
            prev_kept=prev_kept,
            curr_kept=cmp.markets_kept,
            prev_retention=prev_retention,
            curr_retention=cmp.retention_pct,
            recovered=recovered,
            still_missing=still_missing,
            key_markets=self._settings.markets.key_markets,
        )
        await slack.post_thread_reply(event.slack_message_ts, update_text)
        logger.info("Posted %s update for %s", phase.value, event.betpawa_event_id)

    async def _check_weekly_report(self, slack: SlackClient, now: datetime) -> None:
        """Check if it's time to generate the weekly report."""
        assert self._repo is not None
        assert self._market_repo is not None
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

        reporter = WeeklyReporter(self._repo, week_starts=cfg.week_starts, on_time_threshold_seconds=self._settings.thresholds.on_time_threshold_seconds)
        market_reporter = MarketReporter(self._repo, self._market_repo)
        start, end = reporter.compute_report_period(now)

        try:
            # Lifecycle summary (existing)
            summary = await reporter.generate_slack_summary(start, end)

            # Markets summary (new)
            if self._settings.markets.enabled:
                markets_block = await market_reporter.generate_markets_summary_block(
                    start, end
                )
                summary = summary + "\n\n" + markets_block

            await slack.post_summary(summary)

            # Lifecycle CSV (existing)
            csv_content = await reporter.generate_csv(start, end)
            output_dir = Path(cfg.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            csv_path = output_dir / f"weekly-{now.strftime('%Y-%m-%d')}.csv"
            csv_path.write_text(csv_content, encoding="utf-8")

            # Markets aggregate CSV + per-event CSVs (new)
            if self._settings.markets.enabled:
                market_csv = await market_reporter.generate_aggregate_csv(start, end)
                market_csv_path = (
                    output_dir / f"weekly-markets-{now.strftime('%Y-%m-%d')}.csv"
                )
                market_csv_path.write_text(market_csv, encoding="utf-8")

                event_dir = output_dir / "events" / now.strftime("%Y-%m-%d")
                event_dir.mkdir(parents=True, exist_ok=True)
                comparisons = await self._market_repo.get_comparisons_in_date_range(
                    start, end
                )
                for cmp in comparisons:
                    per_event_csv = await market_reporter.generate_per_event_csv(
                        cmp.event_id
                    )
                    event = await self._repo.get_by_id(cmp.event_id)
                    if event is None:
                        continue
                    safe_home = "".join(c if c.isalnum() else "_" for c in event.home_team)
                    safe_away = "".join(c if c.isalnum() else "_" for c in event.away_team)
                    filename = (
                        f"{event.betpawa_event_id}_{safe_home}_vs_{safe_away}.csv"
                    )
                    (event_dir / filename).write_text(per_event_csv, encoding="utf-8")

            self._last_report_date = now
            logger.info("Weekly report generated (lifecycle + markets)")

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
