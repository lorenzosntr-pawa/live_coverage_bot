"""BetPawa API client for fetching live football events."""

import json
import logging
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

import httpx

from live_coverage_bot.clients.models import (
    LiveEvent,
    ProviderID,
    ProviderType,
    UpcomingEvent,
)
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
            from datetime import timedelta

            cutoff_time = datetime.now(tz=UTC) + timedelta(hours=hours_ahead)
            events: list[UpcomingEvent] = []
            skip = 0
            take = 100

            cutoff_crossed = False
            while True:
                query = {
                    "queries": [
                        {
                            "query": {
                                "eventType": "UPCOMING",
                                "categories": ["2"],
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

                page_kept = 0
                # Process each event
                for event_data in event_list:
                    # Get event ID
                    event_id = event_data.get("id", "")
                    if not event_id:
                        continue

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

                    # Sorted ascending by startTime — once we cross cutoff, stop entirely
                    if start_time > cutoff_time:
                        cutoff_crossed = True
                        break

                    # Extract team names from participants
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

                    # Get competition and region
                    competition = event_data.get("competition") or {}
                    competition_name = competition.get("name", "")
                    region = event_data.get("region") or {}
                    country_name = region.get("name") or None

                    # Extract ALL provider IDs
                    widgets = event_data.get("widgets", [])
                    provider_ids = self._extract_provider_ids(widgets)

                    # Create UpcomingEvent with full data
                    events.append(
                        UpcomingEvent(
                            event_id=str(event_id),
                            home_team=home_team,
                            away_team=away_team,
                            competition_name=competition_name,
                            country_name=country_name,
                            start_time=start_time,
                            provider_ids=provider_ids,
                        )
                    )
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
        except Exception as e:
            logger.error("Unexpected error fetching upcoming events: %s", e)
            raise BetPawaError(f"Unexpected error: {e}") from e

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
            take = 100

            while True:
                # Build query JSON for live football events (category 2 = football)
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

        # Get competition info (use 'or {}' to handle explicit null values)
        competition = event_data.get("competition") or {}
        competition_id = str(competition.get("id", ""))
        competition_name = competition.get("name", "")

        # Get country name from region (use 'or {}' to handle explicit null values)
        region = event_data.get("region") or {}
        country_name = region.get("name") or None

        # Get minute from results.display.minute (results can be null for pre-match)
        results = event_data.get("results") or {}
        display = results.get("display") or {}
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

    async def get_event_markets(self, event_id: str) -> list["Market"]:
        """Fetch full event detail including all markets.

        Uses GET /events/{event_id}. Returns the list of Market objects parsed
        from the response. BetPawa's event ID is used directly (no 'bp:' prefix).

        Raises:
            BetPawaError: If the API request fails or parsing fails.
        """
        from live_coverage_bot.models.markets import Market, MarketRow, Selection

        try:
            response = await self._client.get(f"/events/{event_id}")
            response.raise_for_status()
            data = response.json()

            markets: list[Market] = []
            for raw_market in data.get("markets", []):
                mt = raw_market.get("marketType") or {}
                market_type_id = str(mt.get("id", ""))
                if not market_type_id:
                    continue
                market_type_name = mt.get("name", "")
                priority = int(mt.get("priority", 0) or 0)

                rows: list[MarketRow] = []
                for raw_row in raw_market.get("row", []):
                    row_id = str(raw_row.get("id", ""))
                    if not row_id:
                        continue
                    handicap_value = raw_row.get("handicap")
                    handicap = str(handicap_value) if handicap_value is not None else None

                    selections: list[Selection] = []
                    for raw_price in raw_row.get("prices", []):
                        price_id = str(raw_price.get("id", ""))
                        type_id = str(raw_price.get("typeId", ""))
                        raw_price_value = raw_price.get("price")
                        if not price_id or not type_id or raw_price_value is None:
                            continue
                        try:
                            price_value = float(raw_price_value)
                        except (TypeError, ValueError):
                            continue
                        selections.append(Selection(
                            price_id=price_id,
                            name=raw_price.get("name", ""),
                            type_id=type_id,
                            price=price_value,
                            suspended=bool(raw_price.get("suspended", False)),
                        ))

                    rows.append(MarketRow(
                        row_id=row_id,
                        handicap=handicap,
                        selections=selections,
                    ))

                markets.append(Market(
                    market_type_id=market_type_id,
                    market_type_name=market_type_name,
                    priority=priority,
                    rows=rows,
                ))

            return markets
        except httpx.HTTPStatusError as e:
            logger.error("BetPawa event detail API returned error: %s", e.response.status_code)
            raise BetPawaError(f"Event detail API returned status {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error("BetPawa event detail API request failed: %s", e)
            raise BetPawaError(f"Event detail request failed: {e}") from e
        except Exception as e:
            logger.error("Unexpected error fetching event detail %s: %s", event_id, e)
            raise BetPawaError(f"Unexpected error: {e}") from e

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
