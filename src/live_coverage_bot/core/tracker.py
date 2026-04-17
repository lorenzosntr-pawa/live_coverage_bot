"""Event lifecycle tracker — manages state transitions for prematch events."""

import logging
from datetime import datetime

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
    ) -> None:
        self._repo = repo
        self._grace_period_minutes = grace_period_minutes
        self._hard_timeout_minutes = hard_timeout_minutes

    async def register_prematch_events(
        self,
        prematch_feed: list[dict],
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

            transition = await self._evaluate_transition(event, is_in_live_feed, now)
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
            await self._repo.update_status(event.id, EventStatus.LIVE, updated_at=now)
            await self._repo.update_live_fields(
                event.id, first_seen_live=now, transition_delay_sec=delay_sec
            )
            await self._repo.insert_state_change(
                event.id, EventStatus.REMOVED, EventStatus.LIVE, now,
                details="Recovered: appeared in live feed after being marked REMOVED",
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
                details="Recovered: appeared in live feed after being marked REMOVED",
            ))

        return transitions

    async def detect_removed(
        self,
        current_prematch_ids: set[str],
        now: datetime,
        kickoff_buffer_minutes: int = 30,
    ) -> list[RemovedResult]:
        """Detect PREMATCH events that disappeared from the feed before kickoff.

        Events close to kickoff are NOT marked as REMOVED even if they vanish
        from the prematch feed, because BetPawa commonly drops events from
        prematch before they appear in the live feed (data propagation gap).
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
            await self._repo.insert_state_change(
                event_id=event.id,
                old_status=EventStatus.PREMATCH,
                new_status=EventStatus.REMOVED,
                changed_at=now,
                details="Disappeared from prematch feed before kickoff",
            )
            removed.append(RemovedResult(
                event=event,
                old_status=EventStatus.PREMATCH,
                details="Disappeared from prematch feed before kickoff",
            ))
            logger.info(
                "Event removed: %s %s vs %s",
                event.betpawa_event_id, event.home_team, event.away_team,
            )

        return removed

    async def _evaluate_transition(
        self,
        event: TrackedEvent,
        is_in_live_feed: bool,
        now: datetime,
    ) -> TransitionResult | None:
        """Evaluate a single event for state transition."""
        assert event.id is not None
        elapsed_sec = (now - event.scheduled_kickoff).total_seconds()
        elapsed_min = elapsed_sec / 60
        old_status = event.status

        if event.status == EventStatus.PREMATCH and is_in_live_feed:
            delay_sec = max(0, int(elapsed_sec))
            details = f"Went live ({delay_sec // 60}min after kickoff)" if delay_sec > 0 else "Went live on time"
            await self._repo.update_status(event.id, EventStatus.LIVE, updated_at=now)
            await self._repo.update_live_fields(event.id, first_seen_live=now, transition_delay_sec=delay_sec)
            await self._repo.insert_state_change(
                event.id, old_status, EventStatus.LIVE, now, details=details,
            )
            return TransitionResult(
                event=event,
                old_status=old_status,
                new_status=EventStatus.LIVE,
                delay_sec=delay_sec,
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
            await self._repo.update_status(event.id, EventStatus.LIVE, updated_at=now)
            await self._repo.update_live_fields(event.id, first_seen_live=now, transition_delay_sec=delay_sec)
            await self._repo.insert_state_change(
                event.id, old_status, EventStatus.LIVE, now, details=details,
            )
            return TransitionResult(
                event=event,
                old_status=old_status,
                new_status=EventStatus.LIVE,
                delay_sec=delay_sec,
                details=details,
            )

        if event.status == EventStatus.LATE and not is_in_live_feed:
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
