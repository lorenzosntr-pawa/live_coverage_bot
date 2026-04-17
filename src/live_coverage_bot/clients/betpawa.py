"""BetPawa API client for fetching live football events."""

import json
import logging
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self

import httpx

from live_coverage_bot.clients.models import (
    LiveEvent,
    UpcomingEvent,
)
from live_coverage_bot.clients.parsers import (
    parse_event_markets,
    parse_live_event,
    parse_upcoming_event,
)
from live_coverage_bot.config.models import BetPawaConfig
from live_coverage_bot.models.markets import Market

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 100
FOOTBALL_CATEGORY_ID = "2"
EVENTS_ENDPOINT = "/events/lists/by-queries"


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

    async def get_upcoming_events(self, hours_ahead: int = 3) -> list[UpcomingEvent]:
        """Fetch upcoming football events from BetPawa pre-match.

        Paginates through all upcoming events and returns full event data
        for human-readable logging and provider ID matching.

        Args:
            hours_ahead: Only include events starting within this many hours.

        Returns:
            List of UpcomingEvent with full details and all provider IDs.

        Raises:
            BetPawaError: If the API request fails.
        """
        try:
            cutoff_time = datetime.now(tz=UTC) + timedelta(hours=hours_ahead)
            events: list[UpcomingEvent] = []
            skip = 0
            take = DEFAULT_PAGE_SIZE

            cutoff_crossed = False
            while True:
                query = {
                    "queries": [
                        {
                            "query": {
                                "eventType": "UPCOMING",
                                "categories": [FOOTBALL_CATEGORY_ID],
                                "zones": {},
                                "hasOdds": True,
                            },
                            "view": {
                                "marketTypes": ["3743"],
                            },
                            "skip": skip,
                            "take": take,
                            "sort": {
                                "startTime": "ASC",
                            },
                        }
                    ]
                }
                params = {"q": json.dumps(query)}

                response = await self._client.get(
                    EVENTS_ENDPOINT,
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

                page_kept = 0
                # Process each event
                for event_data in event_list:
                    upcoming = parse_upcoming_event(event_data)
                    if upcoming is None:
                        continue

                    # Sorted ascending by startTime — once we cross cutoff, stop entirely
                    if upcoming.start_time > cutoff_time:
                        cutoff_crossed = True
                        break

                    events.append(upcoming)
                    page_kept += 1

                logger.debug(
                    "Prematch page skip=%d returned=%d kept=%d", skip, len(event_list), page_kept
                )

                if cutoff_crossed:
                    break

                # Check if we got fewer events than requested (last page)
                if len(event_list) < take:
                    break

                skip += take

            logger.info("Fetched %d upcoming events (next %dh)", len(events), hours_ahead)
            return events

        except httpx.HTTPStatusError as e:
            logger.error("BetPawa API returned error: %s", e.response.status_code)
            raise BetPawaError(f"API returned status {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error("BetPawa API request failed: %s", e)
            raise BetPawaError(f"Request failed: {e}") from e

    async def get_live_events(self) -> list[LiveEvent]:
        """Fetch all live football events from BetPawa.

        Paginates through all live events to ensure none are missed.

        Returns:
            List of live events with extracted provider IDs.

        Raises:
            BetPawaError: If the API request fails.
        """
        try:
            events: list[LiveEvent] = []
            skip = 0
            take = DEFAULT_PAGE_SIZE

            while True:
                # Build query JSON for live football events (category 2 = football)
                query = {
                    "queries": [
                        {
                            "query": {
                                "eventType": "LIVE",
                                "categories": [FOOTBALL_CATEGORY_ID],
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
                    EVENTS_ENDPOINT,
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

                page_events = self._parse_events(data)
                events.extend(page_events)

                # Check if we got fewer events than requested (last page)
                if len(page_events) < take:
                    break

                skip += take

            return events
        except httpx.HTTPStatusError as e:
            logger.error("BetPawa API returned error: %s", e.response.status_code)
            raise BetPawaError(f"API returned status {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error("BetPawa API request failed: %s", e)
            raise BetPawaError(f"Request failed: {e}") from e

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
                event = parse_live_event(event_data)
                if event:
                    events.append(event)
            except Exception as e:
                logger.warning(
                    "Failed to parse event %s: %s",
                    event_data.get("id", "unknown"),
                    e,
                )

        return events

    async def get_event_markets(self, event_id: str) -> list[Market]:
        """Fetch full event detail including all markets.

        Uses GET /events/{event_id}. Returns the list of Market objects parsed
        from the response. BetPawa's event ID is used directly (no 'bp:' prefix).

        Raises:
            BetPawaError: If the API request fails.
        """
        try:
            response = await self._client.get(f"/events/{event_id}")
            response.raise_for_status()
            data = response.json()
            return parse_event_markets(data)
        except httpx.HTTPStatusError as e:
            logger.error("BetPawa event detail API returned error: %s", e.response.status_code)
            raise BetPawaError(f"Event detail API returned status {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error("BetPawa event detail API request failed: %s", e)
            raise BetPawaError(f"Event detail request failed: {e}") from e

    @staticmethod
    def build_live_betpawa_id_set(events: list[LiveEvent]) -> set[str]:
        """Build a set of BetPawa event IDs (without 'bp:' prefix) from live events.

        BetPawa's own event ID is stable across prematch and live feeds, making it
        the most reliable identifier for matching tracked prematch events against
        the current live feed.
        """
        result: set[str] = set()
        for event in events:
            eid = event.event_id
            if eid.startswith("bp:"):
                eid = eid[3:]
            result.add(eid)
        return result

    @staticmethod
    def upcoming_to_tracker_feed(events: list[UpcomingEvent]) -> list[dict[str, Any]]:
        """Convert UpcomingEvent list to the dict format expected by the tracker."""
        return [
            {
                "betpawa_event_id": event.event_id,
                "home_team": event.home_team,
                "away_team": event.away_team,
                "competition": event.competition_name,
                "country": event.country_name,
                "scheduled_kickoff": event.start_time,
                "provider_ids": event.provider_ids,
            }
            for event in events
        ]

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
