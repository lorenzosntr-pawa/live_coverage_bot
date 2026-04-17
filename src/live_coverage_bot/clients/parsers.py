"""Standalone parsing functions for BetPawa API responses.

These functions are extracted from BetPawaClient to be independently testable
and focused on data transformation rather than HTTP concerns.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from live_coverage_bot.clients.models import (
    LiveEvent,
    ProviderID,
    ProviderType,
    UpcomingEvent,
)
from live_coverage_bot.models.markets import Market, MarketRow, Selection

logger = logging.getLogger(__name__)


def extract_provider_ids(widgets: list[dict[str, Any]]) -> list[ProviderID]:
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


def extract_scores(
    results: dict[str, Any],
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
                    logger.warning("Invalid score value: %r", result_str)
                break

    return home_score, away_score


def parse_live_event(event_data: dict[str, Any]) -> LiveEvent | None:
    """Parse a single live event from API response.

    Args:
        event_data: Event data from API.

    Returns:
        Parsed LiveEvent or None if the event has no id.
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
    home_score, away_score = extract_scores(results)

    # Parse start time
    start_time_str = event_data.get("startTime", "")
    start_time = datetime.now(tz=UTC)
    if start_time_str:
        try:
            start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Invalid startTime: %r, using current time", start_time_str)

    # Extract provider IDs from widgets
    provider_ids = extract_provider_ids(event_data.get("widgets", []))

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


def parse_upcoming_event(event_data: dict[str, Any]) -> UpcomingEvent | None:
    """Parse a single upcoming event from API response.

    Args:
        event_data: Event data from API.

    Returns:
        Parsed UpcomingEvent or None if id or startTime is missing/invalid.
    """
    event_id = event_data.get("id", "")
    if not event_id:
        return None

    start_time_str = event_data.get("startTime", "")
    if not start_time_str:
        return None

    try:
        start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
    except ValueError:
        return None

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
    provider_ids = extract_provider_ids(widgets)

    return UpcomingEvent(
        event_id=str(event_id),
        home_team=home_team,
        away_team=away_team,
        competition_name=competition_name,
        country_name=country_name,
        start_time=start_time,
        provider_ids=provider_ids,
    )


def parse_event_markets(data: dict[str, Any]) -> list[Market]:
    """Parse market data from an event detail API response.

    Args:
        data: Raw JSON response from BetPawa event detail API.

    Returns:
        List of parsed Market objects.
    """
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
