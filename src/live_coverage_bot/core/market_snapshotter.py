"""Market snapshotter — fetches and stores market snapshots on configured windows."""

import asyncio
import logging
import time
from datetime import datetime

from live_coverage_bot.clients.betpawa import BetPawaClient, BetPawaError
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

        if live_transition_event_ids:
            for bp_id in live_transition_event_ids:
                event = await self._events.get_by_betpawa_id(bp_id)
                if event is None or event.id is None:
                    continue
                taken = await self._markets.phases_taken_for_event(event.id)
                if SnapshotPhase.LIVE not in taken:
                    plan.append((event, SnapshotPhase.LIVE))

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
