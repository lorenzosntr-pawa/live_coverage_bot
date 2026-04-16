"""Event lifecycle tracker — manages state transitions for prematch events."""

import logging
from datetime import datetime
from typing import Any

from live_coverage_bot.clients.models import ProviderType
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent

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
        live_provider_ids: set[tuple[ProviderType, str]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Check all active events for state transitions."""
        active_events = await self._repo.get_active_events()
        transitions: list[dict[str, Any]] = []

        for event in active_events:
            assert event.id is not None
            event_live_keys = {(pid.type, pid.id) for pid in event.provider_ids}
            is_in_live_feed = bool(event_live_keys & live_provider_ids)

            transition = await self._evaluate_transition(event, is_in_live_feed, now)
            if transition:
                transitions.append(transition)

        return transitions

    async def detect_removed(
        self,
        current_prematch_ids: set[str],
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Detect PREMATCH events that disappeared from the feed before kickoff."""
        active_events = await self._repo.get_active_events()
        removed: list[dict[str, Any]] = []

        for event in active_events:
            assert event.id is not None
            if event.status != EventStatus.PREMATCH:
                continue
            if now >= event.scheduled_kickoff:
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
            removed.append({
                "betpawa_event_id": event.betpawa_event_id,
                "event": event,
                "old_status": EventStatus.PREMATCH,
                "new_status": EventStatus.REMOVED,
            })
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
    ) -> dict[str, Any] | None:
        """Evaluate a single event for state transition."""
        assert event.id is not None
        elapsed_sec = (now - event.scheduled_kickoff).total_seconds()
        elapsed_min = elapsed_sec / 60
        old_status = event.status

        if event.status == EventStatus.PREMATCH and is_in_live_feed:
            delay_sec = max(0, int(elapsed_sec))
            await self._repo.update_status(event.id, EventStatus.LIVE, updated_at=now)
            await self._repo.update_live_fields(event.id, first_seen_live=now, transition_delay_sec=delay_sec)
            await self._repo.insert_state_change(
                event.id, old_status, EventStatus.LIVE, now,
                details=f"Went live ({delay_sec // 60}min after kickoff)" if delay_sec > 0 else "Went live on time",
            )
            return {
                "betpawa_event_id": event.betpawa_event_id,
                "event": event,
                "old_status": old_status,
                "new_status": EventStatus.LIVE,
                "delay_sec": delay_sec,
            }

        if event.status == EventStatus.PREMATCH and not is_in_live_feed:
            if elapsed_min >= self._grace_period_minutes:
                await self._repo.update_status(event.id, EventStatus.LATE, updated_at=now)
                await self._repo.insert_state_change(
                    event.id, old_status, EventStatus.LATE, now,
                    details=f"{int(elapsed_min)}min past kickoff",
                )
                return {
                    "betpawa_event_id": event.betpawa_event_id,
                    "event": event,
                    "old_status": old_status,
                    "new_status": EventStatus.LATE,
                }

        if event.status == EventStatus.LATE and is_in_live_feed:
            delay_sec = max(0, int(elapsed_sec))
            await self._repo.update_status(event.id, EventStatus.LIVE, updated_at=now)
            await self._repo.update_live_fields(event.id, first_seen_live=now, transition_delay_sec=delay_sec)
            await self._repo.insert_state_change(
                event.id, old_status, EventStatus.LIVE, now,
                details=f"Went live (delay: {delay_sec // 60}min)",
            )
            return {
                "betpawa_event_id": event.betpawa_event_id,
                "event": event,
                "old_status": old_status,
                "new_status": EventStatus.LIVE,
                "delay_sec": delay_sec,
            }

        if event.status == EventStatus.LATE and not is_in_live_feed:
            if elapsed_min >= self._hard_timeout_minutes:
                await self._repo.update_status(event.id, EventStatus.NEVER_LIVE, updated_at=now)
                await self._repo.insert_state_change(
                    event.id, old_status, EventStatus.NEVER_LIVE, now,
                    details=f"Timed out after {int(elapsed_min)}min",
                )
                return {
                    "betpawa_event_id": event.betpawa_event_id,
                    "event": event,
                    "old_status": old_status,
                    "new_status": EventStatus.NEVER_LIVE,
                }

        return None
