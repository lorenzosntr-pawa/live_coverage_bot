"""Slack notification client for sending missing event alerts."""

import logging
from types import TracebackType
from typing import Self

import httpx

from live_coverage_bot.clients.models import LiveEvent
from live_coverage_bot.config.models import SlackConfig

logger = logging.getLogger(__name__)


class SlackError(Exception):
    """Error raised when Slack operations fail."""

    pass


class SlackNotifier:
    """Async client for sending notifications to Slack via webhook.

    Sends alerts for missing events detected in coverage comparison.
    """

    def __init__(self, config: SlackConfig) -> None:
        """Initialize the client with configuration.

        Args:
            config: Slack configuration with webhook URL.
        """
        self._config = config
        self._client = httpx.AsyncClient(timeout=10.0)

    async def send_missing_event_alert(self, event: LiveEvent) -> bool:
        """Send an alert for a missing event to Slack.

        Args:
            event: The live event that is missing from BetPawa.

        Returns:
            True if the alert was sent successfully, False otherwise.
        """
        message = self._format_message(event)

        try:
            response = await self._client.post(
                str(self._config.webhook_url),
                json={"text": message},
            )
            if response.is_success:
                logger.info(
                    "Sent Slack alert for %s vs %s", event.home_team, event.away_team
                )
                return True
            else:
                logger.warning(
                    "Slack webhook returned status %s", response.status_code
                )
                return False
        except httpx.RequestError as e:
            logger.error("Failed to send Slack alert: %s", e)
            return False

    def _format_message(self, event: LiveEvent) -> str:
        """Format event data into a Slack message.

        Args:
            event: The live event to format.

        Returns:
            Formatted message string with mrkdwn formatting.
        """
        # Build match line with optional minute
        match_line = f"*{event.home_team} vs {event.away_team}*"
        if event.minute is not None:
            match_line += f" ({event.minute}')"

        # Build score display
        if event.home_score is not None and event.away_score is not None:
            score = f"{event.home_score}-{event.away_score}"
        else:
            score = "-"

        # Build competition line with optional country prefix
        if event.country_name:
            competition_line = f"{event.country_name} - {event.competition_name}"
        else:
            competition_line = event.competition_name

        return f"🔴 *Missing on BetPawa*\n{match_line}\n{competition_line} | Score: {score}"

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        """Enter async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context manager and close client."""
        await self.close()
