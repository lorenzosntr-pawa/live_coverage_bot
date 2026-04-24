"""Event lifecycle tracker — manages state transitions for prematch events."""

import logging
from datetime import datetime
from typing import Any

from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, RemovedResult, TrackedEvent, TransitionResult

logger = logging.getLogger(__name__)


class EventLifecycleTracker:
    """Tracks events through their prematch-to-live lifecycle using SQLite."""

    def __init__(
        self,
        repo: EventRepository,
        grace_period_minutes: int = 5,
        hard_timeout_minutes: int = 90,
        coverage_late_minute_threshold: int = 5,
    ) -> None:
        self._repo = repo
        self._grace_period_minutes = grace_period_minutes
        self._hard_timeout_minutes = hard_timeout_minutes
        self._coverage_late_minute_threshold = coverage_late_minute_threshold

    async def register_prematch_events(
        self,
        prematch_feed: list[dict[str, Any]],
        now: datetime,
    ) -> list[str]:
        """Register new prematch events from the feed. Returns list of new event IDs."""
        new_ids: list[str] = []
        for item in prematch_feed:
            bp_id = item["betpawa_event_id"]
            existing = await self._repo.get_by_betpawa_id(bp_id)
            if existing is not None:
                continue

            event = TrackedEvent(
                betpawa_event_id=bp_id,
                home_team=item["home_team"],
                away_team=item["away_team"],
                competition=item["competition"],
                competition_id=item.get("competition_id", ""),
                country=item.get("country"),
                scheduled_kickoff=item["scheduled_kickoff"],
                status=EventStatus.PREMATCH,
                provider_ids=item["provider_ids"],
                first_seen_prematch=now,
            )
            await self._repo.insert_event(event)
            new_ids.append(bp_id)
            logger.debug("Registered prematch event: %s %s vs %s", bp_id, event.home_team, event.away_team)

        return new_ids

    async def check_transitions(
        self,
        live_betpawa_ids: set[str],
        now: datetime,
        *,
        live_events_map: dict[str, Any] | None = None,
        prematch_events_map: dict[str, Any] | None = None,
    ) -> list[TransitionResult]:
        """Check all active events for state transitions.

        Matches by BetPawa event ID (stable across prematch and live feeds).
        Also recovers REMOVED events that later appear in the live feed
        (happens when BetPawa drops an event from prematch briefly before
        it surfaces in the live feed — the event was wrongly marked REMOVED).
        """
        from datetime import timedelta

        active_events = await self._repo.get_active_events()
        transitions: list[TransitionResult] = []

        for event in active_events:
            assert event.id is not None
            is_in_live_feed = event.betpawa_event_id in live_betpawa_ids

            transition = await self._evaluate_transition(
                event, is_in_live_feed, now,
                live_events_map=live_events_map,
                prematch_events_map=prematch_events_map,
            )
            if transition:
                transitions.append(transition)

        # Recovery: REMOVED events that now appear in the live feed should
        # transition to LIVE. Look back up to 24h to avoid reviving very old data.
        lookback = now - timedelta(hours=24)
        removed_events = await self._repo.get_recent_removed_events(lookback)
        for event in removed_events:
            assert event.id is not None
            if event.betpawa_event_id not in live_betpawa_ids:
                continue
            elapsed_sec = (now - event.scheduled_kickoff).total_seconds()
            delay_sec = max(0, int(elapsed_sec))
            gap_min = 0
            if event.removed_at:
                gap_min = int((now - event.removed_at).total_seconds() / 60)
            details = (
                f"Reappeared directly in live at {now.strftime('%H:%M')} UTC "
                f"after {gap_min}min gap"
            )
            await self._repo.update_status(event.id, EventStatus.LIVE, updated_at=now)
            await self._repo.update_live_fields(
                event.id, first_seen_live=now, transition_delay_sec=delay_sec
            )
            await self._repo.insert_state_change(
                event.id, EventStatus.REMOVED, EventStatus.LIVE, now,
                details=details,
            )
            logger.info(
                "Recovered REMOVED event to LIVE: %s %s vs %s",
                event.betpawa_event_id, event.home_team, event.away_team,
            )
            transitions.append(TransitionResult(
                event=event,
                old_status=EventStatus.REMOVED,
                new_status=EventStatus.LIVE,
                delay_sec=delay_sec,
                details=details,
            ))

        return transitions

    async def detect_removed(
        self,
        current_prematch_ids: set[str],
        now: datetime,
        kickoff_buffer_minutes: int = 3,
    ) -> list[RemovedResult]:
        """Detect PREMATCH events that disappeared from the feed before kickoff.

        Events close to kickoff are NOT marked as REMOVED even if they vanish
        from the prematch feed, because BetPawa transitions events from
        prematch to live ~1 minute before kickoff.
        Within `kickoff_buffer_minutes` of kickoff, we wait for the event to
        appear in live; post-kickoff, check_transitions/hard_timeout handles it.
        """
        active_events = await self._repo.get_active_events()
        removed: list[RemovedResult] = []

        for event in active_events:
            assert event.id is not None
            if event.status != EventStatus.PREMATCH:
                continue
            if now >= event.scheduled_kickoff:
                continue
            # Within the kickoff buffer — event might be mid-transition to live
            minutes_to_kickoff = (event.scheduled_kickoff - now).total_seconds() / 60
            if minutes_to_kickoff < kickoff_buffer_minutes:
                continue
            if event.betpawa_event_id in current_prematch_ids:
                continue

            await self._repo.update_status(event.id, EventStatus.REMOVED, updated_at=now)
            await self._repo.update_removed_fields(event.id, removed_at=now)
            minutes_before = int(minutes_to_kickoff)
            details = f"Disappeared from prematch at {now.strftime('%H:%M')} UTC, {minutes_before}min before kickoff"
            await self._repo.insert_state_change(
                event_id=event.id,
                old_status=EventStatus.PREMATCH,
                new_status=EventStatus.REMOVED,
                changed_at=now,
                details=details,
            )
            removed.append(RemovedResult(
                event=event,
                old_status=EventStatus.PREMATCH,
                details=details,
            ))
            logger.info(
                "Event removed: %s %s vs %s",
                event.betpawa_event_id, event.home_team, event.away_team,
            )

        return removed

    async def detect_prematch_recovery(
        self,
        current_prematch_ids: set[str],
        now: datetime,
    ) -> list[TransitionResult]:
        """Detect REMOVED events that reappeared in the prematch feed."""
        from datetime import timedelta

        lookback = now - timedelta(hours=24)
        removed_events = await self._repo.get_recent_removed_events(lookback)
        recoveries: list[TransitionResult] = []

        for event in removed_events:
            assert event.id is not None
            if event.betpawa_event_id not in current_prematch_ids:
                continue

            gap_min = 0
            if event.removed_at:
                gap_min = int((now - event.removed_at).total_seconds() / 60)

            await self._repo.update_status(event.id, EventStatus.PREMATCH, updated_at=now)
            details = (
                f"Reappeared in prematch at {now.strftime('%H:%M')} UTC "
                f"after {gap_min}min gap"
            )
            await self._repo.insert_state_change(
                event.id, EventStatus.REMOVED, EventStatus.PREMATCH, now,
                details=details,
            )
            logger.info(
                "Recovered REMOVED to PREMATCH: %s %s vs %s (gap: %dmin)",
                event.betpawa_event_id, event.home_team, event.away_team, gap_min,
            )
            recoveries.append(TransitionResult(
                event=event,
                old_status=EventStatus.REMOVED,
                new_status=EventStatus.PREMATCH,
                details=details,
            ))

        return recoveries

    async def mark_unmonitored(
        self,
        downtime_start: datetime,
        now: datetime,
    ) -> int:
        """Mark active events with past kickoff as UNMONITORED after a downtime gap.

        Any active event (PREMATCH or LATE) whose scheduled_kickoff is before
        `now` gets transitioned to UNMONITORED. Events with future kickoffs
        are left alone for normal monitoring.

        Returns the count of events marked.
        """
        active_events = await self._repo.get_active_events()
        count = 0

        for event in active_events:
            assert event.id is not None
            if event.scheduled_kickoff >= now:
                continue

            old_status = event.status
            details = (
                f"Bot was offline from {downtime_start.strftime('%a %b %d %H:%M')} "
                f"to {now.strftime('%a %b %d %H:%M')} UTC"
            )
            await self._repo.update_status(event.id, EventStatus.UNMONITORED, updated_at=now)
            await self._repo.insert_state_change(
                event.id, old_status, EventStatus.UNMONITORED, now,
                details=details,
            )
            logger.info(
                "Marked as UNMONITORED: %s %s vs %s (kickoff %s)",
                event.betpawa_event_id, event.home_team, event.away_team,
                event.scheduled_kickoff.strftime("%Y-%m-%d %H:%M"),
            )
            count += 1

        return count

    def _classify_live_minute(
        self,
        betpawa_event_id: str,
        live_events_map: dict[str, Any] | None,
    ) -> tuple[int | None, str | None]:
        """Extract live minute and classify late reason from live event data.

        Returns (live_minute, late_reason).
        """
        if live_events_map is None:
            return None, None
        live_event = live_events_map.get(betpawa_event_id)
        if live_event is None:
            return None, None
        minute_str = live_event.minute
        if minute_str is None:
            return None, None
        try:
            minute = int(minute_str)
        except (ValueError, TypeError):
            return None, None
        if minute <= self._coverage_late_minute_threshold:
            return minute, "MATCH_DELAYED"
        else:
            return minute, "COVERAGE_LATE"

    async def _evaluate_transition(
        self,
        event: TrackedEvent,
        is_in_live_feed: bool,
        now: datetime,
        *,
        live_events_map: dict[str, Any] | None = None,
        prematch_events_map: dict[str, Any] | None = None,
    ) -> TransitionResult | None:
        """Evaluate a single event for state transition."""
        assert event.id is not None
        elapsed_sec = (now - event.scheduled_kickoff).total_seconds()
        elapsed_min = elapsed_sec / 60
        old_status = event.status

        if event.status == EventStatus.PREMATCH and is_in_live_feed:
            delay_sec = max(0, int(elapsed_sec))
            details = f"Went live ({delay_sec // 60}min after kickoff)" if delay_sec > 0 else "Went live on time"
            live_minute, late_reason = self._classify_live_minute(event.betpawa_event_id, live_events_map)
            if late_reason == "MATCH_DELAYED":
                late_reason = None  # Normal on-time transition
            await self._repo.update_status(event.id, EventStatus.LIVE, updated_at=now)
            await self._repo.update_live_fields(
                event.id, first_seen_live=now, transition_delay_sec=delay_sec,
                late_reason=late_reason, live_minute=live_minute,
            )
            await self._repo.insert_state_change(
                event.id, old_status, EventStatus.LIVE, now, details=details,
            )
            return TransitionResult(
                event=event,
                old_status=old_status,
                new_status=EventStatus.LIVE,
                delay_sec=delay_sec,
                live_minute=live_minute,
                details=details,
            )

        if event.status == EventStatus.PREMATCH and not is_in_live_feed:
            if elapsed_min >= self._grace_period_minutes:
                details = f"{int(elapsed_min)}min past kickoff"
                await self._repo.update_status(event.id, EventStatus.LATE, updated_at=now)
                await self._repo.insert_state_change(
                    event.id, old_status, EventStatus.LATE, now, details=details,
                )
                return TransitionResult(
                    event=event,
                    old_status=old_status,
                    new_status=EventStatus.LATE,
                    details=details,
                )

        if event.status == EventStatus.LATE and is_in_live_feed:
            delay_sec = max(0, int(elapsed_sec))
            details = f"Went live (delay: {delay_sec // 60}min)"
            live_minute, late_reason = self._classify_live_minute(event.betpawa_event_id, live_events_map)
            await self._repo.update_status(event.id, EventStatus.LIVE, updated_at=now)
            await self._repo.update_live_fields(
                event.id, first_seen_live=now, transition_delay_sec=delay_sec,
                late_reason=late_reason, live_minute=live_minute,
            )
            await self._repo.insert_state_change(
                event.id, old_status, EventStatus.LIVE, now, details=details,
            )
            return TransitionResult(
                event=event,
                old_status=old_status,
                new_status=EventStatus.LIVE,
                delay_sec=delay_sec,
                live_minute=live_minute,
                details=details,
            )

        if event.status == EventStatus.LATE and not is_in_live_feed:
            # Check for kickoff reschedule before hard timeout
            if prematch_events_map is not None:
                prematch_event = prematch_events_map.get(event.betpawa_event_id)
                if prematch_event is not None:
                    time_diff = abs((prematch_event.start_time - event.scheduled_kickoff).total_seconds())
                    if time_diff > 60:  # More than 1 minute difference
                        old_kickoff_str = event.scheduled_kickoff.strftime("%H:%M")
                        new_kickoff_str = prematch_event.start_time.strftime("%H:%M")
                        details = f"Kickoff rescheduled: {old_kickoff_str} \u2192 {new_kickoff_str} UTC"
                        await self._repo.update_scheduled_kickoff(
                            event.id, new_kickoff=prematch_event.start_time,
                            late_reason="KICKOFF_RESCHEDULED", updated_at=now,
                        )
                        await self._repo.update_status(event.id, EventStatus.PREMATCH, updated_at=now)
                        await self._repo.insert_state_change(
                            event.id, old_status, EventStatus.PREMATCH, now, details=details,
                        )
                        return TransitionResult(
                            event=event,
                            old_status=old_status,
                            new_status=EventStatus.PREMATCH,
                            details=details,
                        )

            if elapsed_min >= self._hard_timeout_minutes:
                details = f"Timed out after {int(elapsed_min)}min"
                await self._repo.update_status(event.id, EventStatus.NEVER_LIVE, updated_at=now)
                await self._repo.insert_state_change(
                    event.id, old_status, EventStatus.NEVER_LIVE, now, details=details,
                )
                return TransitionResult(
                    event=event,
                    old_status=old_status,
                    new_status=EventStatus.NEVER_LIVE,
                    details=details,
                )

        return None
