"""Monitoring loop orchestration for live event coverage."""

import asyncio
import logging

from live_coverage_bot.clients import (
    BetPawaClient,
    BetPawaError,
    SlackNotifier,
    SportyBetClient,
    SportyBetError,
)
from live_coverage_bot.config import Settings
from live_coverage_bot.core.matcher import EventMatcher
from live_coverage_bot.core.tracker import AlertTracker

logger = logging.getLogger(__name__)


class MonitoringLoop:
    """Orchestrates the monitoring process for missing live events.

    Polls SportyBet and BetPawa APIs, detects missing events,
    and sends Slack alerts with deduplication.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize the monitoring loop with settings.

        Args:
            settings: Application settings including polling interval and API configs.
        """
        self._settings = settings
        self._tracker = AlertTracker()
        self._matcher = EventMatcher()

    async def run(self) -> None:
        """Run the monitoring loop until interrupted.

        Initializes API clients as async context managers and polls
        at the configured interval.
        """
        async with (
            SportyBetClient(self._settings.sportybet) as sportybet,
            BetPawaClient(self._settings.betpawa) as betpawa,
            SlackNotifier(self._settings.slack) as slack,
        ):
            logger.info(
                "Monitoring loop started (polling every %ds)",
                self._settings.polling_interval_seconds,
            )

            while True:
                try:
                    await self._poll_cycle(sportybet, betpawa, slack)
                except Exception as e:
                    # Catch unexpected errors to keep loop running
                    logger.exception("Unexpected error in poll cycle: %s", e)

                await asyncio.sleep(self._settings.polling_interval_seconds)

    async def _poll_cycle(
        self,
        sportybet: SportyBetClient,
        betpawa: BetPawaClient,
        slack: SlackNotifier,
    ) -> None:
        """Execute a single poll cycle.

        Fetches events from both platforms, finds missing events,
        and sends alerts for new missing events.

        Args:
            sportybet: SportyBet API client.
            betpawa: BetPawa API client.
            slack: Slack notification client.
        """
        # Fetch events from SportyBet
        try:
            sportybet_events = await sportybet.get_live_events()
        except SportyBetError as e:
            logger.warning("SportyBet fetch failed: %s", e)
            sportybet_events = []

        # Fetch events from BetPawa
        try:
            betpawa_events = await betpawa.get_live_events()
        except BetPawaError as e:
            logger.warning("BetPawa fetch failed: %s", e)
            betpawa_events = []

        # Skip matching if either fetch failed completely
        if not sportybet_events or not betpawa_events:
            logger.debug(
                "Skipping match: SportyBet=%d, BetPawa=%d events",
                len(sportybet_events),
                len(betpawa_events),
            )
            return

        # Find missing events
        missing_events = self._matcher.find_missing_events(
            sportybet_events, betpawa_events
        )

        # Send alerts for new missing events
        alerted_count = 0
        for event in missing_events:
            if self._tracker.has_been_alerted(event.event_id):
                logger.debug("Already alerted: %s", event.event_id)
                continue

            # Skip events not yet in-play (no minute set)
            if event.minute is None:
                logger.debug("Skipping pre-match event: %s", event.event_id)
                continue

            # Send Slack alert
            success = await slack.send_missing_event_alert(event)
            if success:
                self._tracker.mark_alerted(event.event_id)
                alerted_count += 1
            else:
                # Don't mark as alerted on failure - will retry next cycle
                logger.warning("Failed to alert for event: %s", event.event_id)

        logger.info(
            "Poll cycle: %d SportyBet, %d BetPawa, %d missing, %d new alerts",
            len(sportybet_events),
            len(betpawa_events),
            len(missing_events),
            alerted_count,
        )
