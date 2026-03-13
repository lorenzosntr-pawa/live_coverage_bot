"""SportyBet API client for fetching live football events."""

import logging
import time
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, ClassVar, Self

import httpx

from live_coverage_bot.clients.models import LiveEvent, ProviderID, ProviderType
from live_coverage_bot.config.models import SportyBetConfig

logger = logging.getLogger(__name__)


class SportyBetError(Exception):
    """Error raised when SportyBet API operations fail."""

    pass


class SportyBetClient:
    """Async client for SportyBet API.

    Fetches live football events with provider ID extraction.
    """

    # Required headers for SportyBet API (from reference implementation)
    DEFAULT_HEADERS: ClassVar[dict[str, str]] = {
        "clientid": "web",
        "platform": "web",
        "operid": "2",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    def __init__(self, config: SportyBetConfig) -> None:
        """Initialize the client with configuration.

        Args:
            config: SportyBet API configuration.
        """
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=10.0,
            headers=self.DEFAULT_HEADERS,
        )

    async def get_live_events(self) -> list[LiveEvent]:
        """Fetch all live football events from SportyBet.

        Returns:
            List of live events with extracted provider IDs.

        Raises:
            SportyBetError: If the API request fails.
        """
        try:
            # Add timestamp parameter (required by SportyBet API)
            params = {
                "sportId": "sr:sport:1",
                "_t": str(int(time.time() * 1000)),
            }
            response = await self._client.get(
                "/liveOrPrematchEvents",
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            # Check for successful response (bizCode 10000)
            biz_code = data.get("bizCode")
            if biz_code != 10000:
                logger.warning("SportyBet API returned bizCode=%s", biz_code)
                return []

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
            List of parsed LiveEvent models (deduplicated by event_id).
        """
        # Use dict to deduplicate - same event can appear in multiple tournaments
        events_by_id: dict[str, LiveEvent] = {}

        # Response structure: data[] contains tournaments/leagues
        # Each tournament has events[] array
        for tournament in data.get("data", []):
            competition_id = tournament.get("id", "")
            competition_name = tournament.get("name", "")
            country_name = tournament.get("categoryName") or None

            for event_data in tournament.get("events", []):
                try:
                    event = self._parse_single_event(
                        event_data, competition_id, competition_name, country_name
                    )
                    if event and event.event_id not in events_by_id:
                        events_by_id[event.event_id] = event
                except Exception as e:
                    logger.warning(
                        "Failed to parse event %s: %s",
                        event_data.get("eventId", "unknown"),
                        e,
                    )

        return list(events_by_id.values())

    def _parse_single_event(
        self,
        event_data: dict[str, Any],
        competition_id: str,
        competition_name: str,
        country_name: str | None,
    ) -> LiveEvent | None:
        """Parse a single event from API response.

        Args:
            event_data: Event data from API.
            competition_id: Tournament/competition ID.
            competition_name: Tournament/competition name.
            country_name: Country/category name from tournament.

        Returns:
            Parsed LiveEvent or None if parsing fails.
        """
        event_id = event_data.get("eventId", "")
        if not event_id:
            return None

        # Extract team names from homeTeamName and awayTeamName
        home_team = event_data.get("homeTeamName", "")
        away_team = event_data.get("awayTeamName", "")

        # Filter SRL test matches (both teams contain "SRL")
        if "SRL" in home_team.upper() and "SRL" in away_team.upper():
            logger.debug("Skipping SRL test match: %s vs %s", home_team, away_team)
            return None

        # Get score from setScore field (format: "1:0" or "1-0")
        set_score = event_data.get("setScore", "")
        home_score: int | None = None
        away_score: int | None = None
        if set_score:
            score_parts = set_score.replace(":", "-").split("-")
            if len(score_parts) == 2:
                try:
                    home_score = int(score_parts[0].strip())
                    away_score = int(score_parts[1].strip())
                except ValueError:
                    pass

        # Get minute from playedSeconds field (format: "MM:SS" like "09:05")
        played_seconds = event_data.get("playedSeconds", "")
        minute: str | None = None
        if played_seconds:
            try:
                # Extract minutes from MM:SS format
                minute = played_seconds.split(":")[0]
            except (IndexError, ValueError):
                pass

        # Parse start time (estimateStartTime is in milliseconds)
        start_time_ms = event_data.get("estimateStartTime", 0)
        start_time = datetime.fromtimestamp(start_time_ms / 1000, tz=UTC)

        # Extract provider IDs from liveSource field
        provider_ids = self._extract_live_source(event_data)

        event = LiveEvent(
            event_id=event_id,
            home_team=home_team,
            away_team=away_team,
            competition_id=competition_id,
            competition_name=competition_name,
            country_name=country_name,
            minute=minute,
            home_score=home_score,
            away_score=away_score,
            start_time=start_time,
        )

        # Set provider IDs from liveSource (overrides event_id parsing)
        if provider_ids:
            event._provider_ids_override = provider_ids

        return event

    def _extract_live_source(self, event_data: dict[str, Any]) -> list[ProviderID]:
        """Extract provider IDs from eventSource.liveSource field.

        Args:
            event_data: Event data from API.

        Returns:
            List of ProviderID objects extracted from liveSource.
        """
        event_source = event_data.get("eventSource") or {}
        live_source = event_source.get("liveSource") or {}
        source_type = live_source.get("sourceType", "")
        source_id = live_source.get("sourceId", "")

        if not source_type or not source_id:
            return []

        # Map SportyBet source types to our ProviderType enum
        type_mapping = {
            "BET_RADAR": ProviderType.SPORTRADAR,
            "BETRADAR": ProviderType.SPORTRADAR,
            "BET_GENIUS": ProviderType.GENIUSSPORTS,
            "GENIUS_SPORTS": ProviderType.GENIUSSPORTS,
            "GENIUSSPORTS": ProviderType.GENIUSSPORTS,
        }

        provider_type = type_mapping.get(source_type.upper())
        if provider_type:
            # Strip 11111111 prefix from GeniusSports IDs (they add 8 ones)
            source_id_str = str(source_id)
            if provider_type == ProviderType.GENIUSSPORTS and source_id_str.startswith("11111111"):
                source_id_str = source_id_str[8:]
            return [ProviderID(type=provider_type, id=source_id_str)]

        logger.debug("Unknown liveSource type: %s", source_type)
        return []

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
