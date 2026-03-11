"""SportyBet API client for fetching live football events."""

import logging
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Self

import httpx

from live_coverage_bot.clients.models import LiveEvent
from live_coverage_bot.config.models import SportyBetConfig

logger = logging.getLogger(__name__)


class SportyBetError(Exception):
    """Error raised when SportyBet API operations fail."""

    pass


class SportyBetClient:
    """Async client for SportyBet API.

    Fetches live football events with provider ID extraction.
    """

    def __init__(self, config: SportyBetConfig) -> None:
        """Initialize the client with configuration.

        Args:
            config: SportyBet API configuration.
        """
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=10.0,
        )

    async def get_live_events(self) -> list[LiveEvent]:
        """Fetch all live football events from SportyBet.

        Returns:
            List of live events with extracted provider IDs.

        Raises:
            SportyBetError: If the API request fails.
        """
        try:
            response = await self._client.get(
                "/liveOrPrematchEvents",
                params={"sportId": "sr:sport:1"},
            )
            response.raise_for_status()
            data = response.json()
            return self._parse_events(data)
        except httpx.HTTPStatusError as e:
            logger.error("SportyBet API returned error: %s", e.response.status_code)
            raise SportyBetError(f"API returned status {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error("SportyBet API request failed: %s", e)
            raise SportyBetError(f"Request failed: {e}") from e
        except Exception as e:
            logger.error("Unexpected error fetching live events: %s", e)
            raise SportyBetError(f"Unexpected error: {e}") from e

    def _parse_events(self, data: dict[str, Any]) -> list[LiveEvent]:
        """Parse API response into LiveEvent models.

        Args:
            data: Raw JSON response from SportyBet API.

        Returns:
            List of parsed LiveEvent models.
        """
        events: list[LiveEvent] = []

        # Response structure: data[] contains tournaments/leagues
        # Each tournament has events[] array
        for tournament in data.get("data", []):
            competition_id = tournament.get("id", "")
            competition_name = tournament.get("name", "")

            for event_data in tournament.get("events", []):
                try:
                    event = self._parse_single_event(
                        event_data, competition_id, competition_name
                    )
                    if event:
                        events.append(event)
                except Exception as e:
                    logger.warning(
                        "Failed to parse event %s: %s",
                        event_data.get("eventId", "unknown"),
                        e,
                    )

        return events

    def _parse_single_event(
        self, event_data: dict[str, Any], competition_id: str, competition_name: str
    ) -> LiveEvent | None:
        """Parse a single event from API response.

        Args:
            event_data: Event data from API.
            competition_id: Tournament/competition ID.
            competition_name: Tournament/competition name.

        Returns:
            Parsed LiveEvent or None if parsing fails.
        """
        event_id = event_data.get("eventId", "")
        if not event_id:
            return None

        # Extract team names from homeTeamName and awayTeamName
        home_team = event_data.get("homeTeamName", "")
        away_team = event_data.get("awayTeamName", "")

        # Get game info for score and minute
        game_info = event_data.get("gameInfo", {})
        live_score = game_info.get("liveScore", "")
        minute = game_info.get("minute")

        # Parse score if available (format: "1-0" or "1:0")
        home_score: int | None = None
        away_score: int | None = None
        if live_score:
            score_parts = live_score.replace(":", "-").split("-")
            if len(score_parts) == 2:
                try:
                    home_score = int(score_parts[0].strip())
                    away_score = int(score_parts[1].strip())
                except ValueError:
                    pass

        # Parse start time (estimateStartTime is in milliseconds)
        start_time_ms = event_data.get("estimateStartTime", 0)
        start_time = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)

        return LiveEvent(
            event_id=event_id,
            home_team=home_team,
            away_team=away_team,
            competition_id=competition_id,
            competition_name=competition_name,
            minute=str(minute) if minute is not None else None,
            home_score=home_score,
            away_score=away_score,
            start_time=start_time,
        )

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
