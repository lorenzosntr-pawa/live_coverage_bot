"""Market snapshotter — fetches and stores market snapshots on configured windows."""

import asyncio
import logging
import time
from datetime import datetime

from live_coverage_bot.clients.betpawa import BetPawaClient, BetPawaError
from live_coverage_bot.clients.parsers import parse_match_state
from live_coverage_bot.config.models import MarketsConfig
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.models.markets import (
    MarketSnapshot,
    SnapshotPhase,
)

logger = logging.getLogger(__name__)


class MarketSnapshotter:
    """Decides when to take market snapshots and executes them."""

    def __init__(
        self,
        event_repo: EventRepository,
        market_repo: MarketRepository,
        betpawa: BetPawaClient,
        config: MarketsConfig,
    ) -> None:
        self._events = event_repo
        self._markets = market_repo
        self._betpawa = betpawa
        self._config = config

    async def run_cycle(
        self,
        now: datetime,
        live_transition_event_ids: set[str],
    ) -> list[tuple[int, SnapshotPhase]]:
        """Run one snapshot decision cycle.

        Args:
            now: Current UTC time.
            live_transition_event_ids: BetPawa event IDs that transitioned to LIVE this cycle.

        Returns:
            List of (event_id, phase) tuples for snapshots that were taken.
        """
        if not self._config.enabled:
            return []

        # Collect all (event, phase) pairs that need a snapshot this cycle.
        plan: list[tuple[TrackedEvent, SnapshotPhase]] = []
        active = await self._events.get_active_events()
        for event in active:
            assert event.id is not None
            taken = await self._markets.phases_taken_for_event(event.id)
            for phase in self._prematch_phases_due(event, now):
                if phase not in taken:
                    plan.append((event, phase))

        # Follow-up live snapshots (LIVE_2, LIVE_5) for events already live
        for event in active:
            if event.status != EventStatus.LIVE or event.id is None:
                continue
            taken = await self._markets.phases_taken_for_event(event.id)
            for phase in self._live_follow_up_phases_due(event, now):
                if phase not in taken:
                    plan.append((event, phase))

        if live_transition_event_ids:
            for bp_id in live_transition_event_ids:
                event = await self._events.get_by_betpawa_id(bp_id)
                if event is None or event.id is None:
                    continue
                taken = await self._markets.phases_taken_for_event(event.id)
                if SnapshotPhase.LIVE_0 not in taken:
                    plan.append((event, SnapshotPhase.LIVE_0))

        if not plan:
            return []

        results = await asyncio.gather(
            *[self._take_snapshot(event, phase, now) for event, phase in plan],
            return_exceptions=False,
        )
        return [r for r in results if r is not None]

    def _prematch_phases_due(
        self, event: TrackedEvent, now: datetime
    ) -> list[SnapshotPhase]:
        """Return prematch phases whose window currently covers `now` for this event."""
        if event.status not in (EventStatus.PREMATCH, EventStatus.LATE):
            return []

        windows = self._config.snapshot_windows
        minutes_to_kickoff = (event.scheduled_kickoff - now).total_seconds() / 60.0
        phases: list[SnapshotPhase] = []

        def in_window(window: list[int]) -> bool:
            hi, lo = window[0], window[1]
            return lo <= minutes_to_kickoff <= hi

        if in_window(windows.prematch_60_min_before):
            phases.append(SnapshotPhase.PREMATCH_60)
        if in_window(windows.prematch_15_min_before):
            phases.append(SnapshotPhase.PREMATCH_15)
        if in_window(windows.prematch_1_min_before):
            phases.append(SnapshotPhase.PREMATCH_1)
        return phases

    def _live_follow_up_phases_due(
        self, event: TrackedEvent, now: datetime
    ) -> list[SnapshotPhase]:
        """Return follow-up live phases that are due for this event."""
        if event.status != EventStatus.LIVE or event.first_seen_live is None:
            return []

        elapsed_min = (now - event.first_seen_live).total_seconds() / 60.0

        # Map config offsets to phase values (skip 0 — that's LIVE_0, taken on transition)
        offset_phase_map: dict[int, SnapshotPhase] = {
            2: SnapshotPhase.LIVE_2,
            5: SnapshotPhase.LIVE_5,
        }

        phases: list[SnapshotPhase] = []
        for offset in self._config.live_snapshot_offsets_minutes:
            if offset == 0:
                continue
            phase = offset_phase_map.get(offset)
            if phase and elapsed_min >= offset:
                phases.append(phase)

        return phases

    async def _take_snapshot(
        self, event: TrackedEvent, phase: SnapshotPhase, now: datetime
    ) -> tuple[int, SnapshotPhase] | None:
        assert event.id is not None
        started = time.monotonic()
        try:
            markets = await self._betpawa.get_event_markets(event.betpawa_event_id)
        except BetPawaError as e:
            logger.warning(
                "Market snapshot fetch failed for %s %s: %s",
                event.betpawa_event_id, phase.value, e,
            )
            return None

        duration_ms = int((time.monotonic() - started) * 1000)

        match_state = None
        if phase.is_live:
            try:
                response = await self._betpawa._client.get(f"/events/{event.betpawa_event_id}")
                response.raise_for_status()
                match_state = parse_match_state(response.json())
            except Exception:
                logger.debug("Could not fetch match state for %s", event.betpawa_event_id)

        total_selections = sum(len(r.selections) for m in markets for r in m.rows)
        suspended = sum(
            1 for m in markets for r in m.rows for s in r.selections if s.suspended
        )

        snapshot = MarketSnapshot(
            event_id=event.id,
            phase=phase,
            taken_at=now,
            markets=markets,
            total_market_count=len(markets),
            total_selection_count=total_selections,
            suspended_count=suspended,
            match_state=match_state,
        )
        try:
            await self._markets.insert_snapshot(snapshot, fetch_duration_ms=duration_ms)
        except Exception as e:
            logger.warning(
                "Snapshot insert failed for event %s phase %s: %s",
                event.betpawa_event_id, phase.value, e,
            )
            return None

        logger.info(
            "Snapshot taken: %s %s vs %s (%s) \u2014 %d markets",
            event.betpawa_event_id, event.home_team, event.away_team,
            phase.value, len(markets),
        )
        return (event.id, phase)
