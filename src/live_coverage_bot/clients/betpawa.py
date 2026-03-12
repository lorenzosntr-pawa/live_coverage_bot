"""BetPawa API client for fetching live football events."""

import json
import logging
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

import httpx

from live_coverage_bot.clients.models import LiveEvent, ProviderID, ProviderType
from live_coverage_bot.config.models import BetPawaConfig

logger = logging.getLogger(__name__)


class BetPawaError(Exception):
    """Error raised when BetPawa API operations fail."""

    pass


class BetPawaClient:
    """Async client for BetPawa API.

    Fetches live football events with provider ID extraction from widgets.
    """

    def __init__(self, config: BetPawaConfig) -> None:
        """Initialize the client with configuration.

        Args:
            config: BetPawa API configuration.
        """
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=10.0,
            headers={
                "x-pawa-brand": config.brand,
                "x-pawa-language": config.language,
                "devicetype": config.device_type,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        )

    async def get_upcoming_events(self, hours_ahead: int = 3) -> dict[str, datetime]:
        """Fetch upcoming football events from BetPawa pre-match.

        Paginates through all upcoming events and returns provider IDs for events
        starting within the specified time window.

        Args:
            hours_ahead: Only include events starting within this many hours.

        Returns:
            Dict mapping provider ID keys (e.g., "SPORTRADAR:12345") to start times.

        Raises:
            BetPawaError: If the API request fails.
        """
        try:
            from datetime import timedelta

            cutoff_time = datetime.now(tz=UTC) + timedelta(hours=hours_ahead)
            all_provider_ids: dict[str, datetime] = {}
            skip = 0
            take = 200

            while True:
                query = {
                    "queries": [
                        {
                            "query": {
                                "eventType": "UPCOMING",
                                "categories": ["2"],
                                "zones": {},
                            },
                            "view": {
                                "marketTypes": ["3743"],
                            },
                            "skip": skip,
                            "take": take,
                            "sort": {
                                "competitionPriority": "DESC",
                            },
                        }
                    ]
                }
                params = {"q": json.dumps(query)}

                response = await self._client.get(
                    "/events/lists/by-queries",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

                # Parse response
                responses = data.get("responses", [])
                if not responses:
                    break

                event_list = responses[0].get("responses", [])
                if not event_list:
                    break

                # Process each event
                for event_data in event_list:
                    # Parse start time
                    start_time_str = event_data.get("startTime", "")
                    if not start_time_str:
                        continue

                    try:
                        start_time = datetime.fromisoformat(
                            start_time_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        continue

                    # Filter by time window
                    if start_time > cutoff_time:
                        continue

                    # Extract provider IDs
                    widgets = event_data.get("widgets", [])
                    provider_ids = self._extract_provider_ids(widgets)

                    # Add to result
                    for pid in provider_ids:
                        key = f"{pid.type.value}:{pid.id}"
                        all_provider_ids[key] = start_time

                # Check if we got fewer events than requested (last page)
                if len(event_list) < take:
                    break

                skip += take

            return all_provider_ids

        except httpx.HTTPStatusError as e:
            logger.error("BetPawa API returned error: %s", e.response.status_code)
            raise BetPawaError(f"API returned status {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error("BetPawa API request failed: %s", e)
            raise BetPawaError(f"Request failed: {e}") from e
        except Exception as e:
            logger.error("Unexpected error fetching upcoming events: %s", e)
            raise BetPawaError(f"Unexpected error: {e}") from e

    async def get_live_events(self) -> list[LiveEvent]:
        """Fetch all live football events from BetPawa.

        Returns:
            List of live events with extracted provider IDs.

        Raises:
            BetPawaError: If the API request fails.
        """
        try:
            # Build query JSON for live football events (category 2 = football)
            # Query format must match BetPawa's expected structure exactly
            query = {
                "queries": [
                    {
                        "query": {
                            "eventType": "LIVE",
                            "categories": ["2"],
                            "zones": {},
                        },
                        "view": {
                            "marketTypes": ["3743"],
                        },
                        "skip": 0,
                        "take": 100,
                        "sort": {
                            "competitionPriority": "DESC",
                        },
                    }
                ]
            }
            params = {"q": json.dumps(query)}

            response = await self._client.get(
                "/events/lists/by-queries",
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            return self._parse_events(data)
        except httpx.HTTPStatusError as e:
            logger.error("BetPawa API returned error: %s", e.response.status_code)
            raise BetPawaError(f"API returned status {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error("BetPawa API request failed: %s", e)
            raise BetPawaError(f"Request failed: {e}") from e
        except Exception as e:
            logger.error("Unexpected error fetching live events: %s", e)
            raise BetPawaError(f"Unexpected error: {e}") from e

    def _parse_events(self, data: dict[str, Any]) -> list[LiveEvent]:
        """Parse API response into LiveEvent models.

        Args:
            data: Raw JSON response from BetPawa API.

        Returns:
            List of parsed LiveEvent models.
        """
        events: list[LiveEvent] = []

        # Response structure: responses[0].responses[] contains events
        responses = data.get("responses", [])
        if not responses:
            return events

        event_list = responses[0].get("responses", [])

        for event_data in event_list:
            try:
                event = self._parse_single_event(event_data)
                if event:
                    events.append(event)
            except Exception as e:
                logger.warning(
                    "Failed to parse event %s: %s",
                    event_data.get("id", "unknown"),
                    e,
                )

        return events

    def _parse_single_event(self, event_data: dict[str, Any]) -> LiveEvent | None:
        """Parse a single event from API response.

        Args:
            event_data: Event data from API.

        Returns:
            Parsed LiveEvent or None if parsing fails.
        """
        event_id = event_data.get("id", "")
        if not event_id:
            return None

        # Prefix with bp: to distinguish from SportyBet IDs
        event_id = f"bp:{event_id}"

        # Extract team names from participants array (position 1 = home, 2 = away)
        participants = event_data.get("participants", [])
        home_team = ""
        away_team = ""
        for participant in participants:
            position = participant.get("position")
            name = participant.get("name", "")
            if position == 1:
                home_team = name
            elif position == 2:
                away_team = name

        # Get competition info
        competition = event_data.get("competition", {})
        competition_id = str(competition.get("id", ""))
        competition_name = competition.get("name", "")

        # Get country name from region
        region = event_data.get("region", {})
        country_name = region.get("name") or None

        # Get minute from results.display.minute
        results = event_data.get("results", {})
        display = results.get("display", {})
        minute = display.get("minute")

        # Extract scores from participantPeriodResults
        home_score, away_score = self._extract_scores(results)

        # Parse start time
        start_time_str = event_data.get("startTime", "")
        start_time = datetime.now(tz=UTC)
        if start_time_str:
            try:
                start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            except ValueError:
                pass

        # Extract provider IDs from widgets
        provider_ids = self._extract_provider_ids(event_data.get("widgets", []))

        # Create LiveEvent and set provider ID override (PrivateAttr must be set after init)
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
        if provider_ids:
            event._provider_ids_override = provider_ids

        return event

    def _extract_scores(
        self, results: dict[str, Any]
    ) -> tuple[int | None, int | None]:
        """Extract home and away scores from participantPeriodResults.

        Args:
            results: The results object from event data.

        Returns:
            Tuple of (home_score, away_score).
        """
        home_score: int | None = None
        away_score: int | None = None

        participant_results = results.get("participantPeriodResults", [])

        for pr in participant_results:
            participant = pr.get("participant", {})
            participant_type = participant.get("type")
            period_results = pr.get("periodResults", [])

            # Find FULL_TIME_EXCLUDING_OVERTIME period
            for period_result in period_results:
                period = period_result.get("period", {})
                if period.get("slug") == "FULL_TIME_EXCLUDING_OVERTIME":
                    result_str = period_result.get("result", "")
                    try:
                        score = int(result_str)
                        if participant_type == "HOME":
                            home_score = score
                        elif participant_type == "AWAY":
                            away_score = score
                    except ValueError:
                        pass
                    break

        return home_score, away_score

    def _extract_provider_ids(self, widgets: list[dict[str, Any]]) -> list[ProviderID]:
        """Extract provider IDs from widgets array.

        Args:
            widgets: List of widget objects with type and id fields.

        Returns:
            Deduplicated list of ProviderID objects.
        """
        seen: set[tuple[str, str]] = set()
        provider_ids: list[ProviderID] = []

        for widget in widgets:
            widget_type = widget.get("type", "")
            widget_id = widget.get("id", "")

            if not widget_type or not widget_id:
                continue

            # Only process known provider types
            if widget_type not in ("SPORTRADAR", "GENIUSSPORTS"):
                continue

            # Deduplicate by (type, id) pair
            key = (widget_type, widget_id)
            if key in seen:
                continue
            seen.add(key)

            try:
                provider_type = ProviderType(widget_type)
                provider_ids.append(ProviderID(type=provider_type, id=widget_id))
            except ValueError:
                logger.warning("Unknown provider type: %s", widget_type)

        return provider_ids

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
