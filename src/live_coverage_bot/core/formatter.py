"""Human-readable event formatting for logging output."""

from collections import defaultdict

from live_coverage_bot.clients.models import LiveEvent, UpcomingEvent


def format_events_by_tournament(
    events: list[LiveEvent] | list[UpcomingEvent],
) -> str:
    """Format events grouped by tournament for human-readable logging.

    Args:
        events: List of LiveEvent or UpcomingEvent to format.

    Returns:
        Formatted string with events grouped by tournament.
    """
    if not events:
        return "  (no events)"

    # Group by tournament key (country - competition or just competition)
    groups: dict[str, list[LiveEvent | UpcomingEvent]] = defaultdict(list)
    for event in events:
        if event.country_name:
            key = f"{event.country_name} - {event.competition_name}"
        else:
            key = event.competition_name or "Unknown"
        groups[key].append(event)

    lines: list[str] = []
    for tournament, tournament_events in sorted(groups.items()):
        lines.append(f"  {tournament}: {len(tournament_events)} events")
        for event in tournament_events:
            lines.append(f"    {_format_single_event(event)}")

    return "\n".join(lines)


def _format_single_event(event: LiveEvent | UpcomingEvent) -> str:
    """Format a single event line.

    Args:
        event: LiveEvent or UpcomingEvent to format.

    Returns:
        Single line format: "Home vs Away | X-X | MM'" (live) or "Home vs Away @ HH:MM" (upcoming)
    """
    match_line = f"{event.home_team} vs {event.away_team}"

    if isinstance(event, LiveEvent):
        # Live event: show score and minute
        score = ""
        if event.home_score is not None and event.away_score is not None:
            score = f" | {event.home_score}-{event.away_score}"
        minute = ""
        if event.minute:
            minute = f" | {event.minute}'"
        return f"{match_line}{score}{minute}"
    else:
        # Upcoming event: show kickoff time
        kickoff = event.start_time.strftime("%H:%M")
        return f"{match_line} @ {kickoff}"


def format_poll_summary(
    sportybet_events: list[LiveEvent],
    betpawa_events: list[LiveEvent],
    missing_events: list[LiveEvent],
    alerted_count: int,
) -> str:
    """Format complete poll cycle summary for logging.

    Args:
        sportybet_events: Live events from SportyBet.
        betpawa_events: Live events from BetPawa.
        missing_events: Events missing from BetPawa.
        alerted_count: Number of new alerts sent.

    Returns:
        Multi-line formatted summary with event groups.
    """
    lines: list[str] = []

    # SportyBet section
    lines.append(f"=== SportyBet ({len(sportybet_events)}) ===")
    lines.append(format_events_by_tournament(sportybet_events))

    # BetPawa section
    lines.append(f"=== BetPawa ({len(betpawa_events)}) ===")
    lines.append(format_events_by_tournament(betpawa_events))

    # Missing section (only if there are missing events)
    if missing_events:
        lines.append(f"=== Missing ({len(missing_events)}) ===")
        lines.append(format_events_by_tournament(missing_events))

    # Summary line
    lines.append(
        f"Poll: {len(sportybet_events)} SportyBet, "
        f"{len(betpawa_events)} BetPawa, "
        f"{len(missing_events)} missing, "
        f"{alerted_count} alerts"
    )

    return "\n".join(lines)


def format_prematch_cache(events: list[UpcomingEvent]) -> str:
    """Format pre-match cache contents for logging.

    Args:
        events: Cached upcoming events.

    Returns:
        Formatted string showing cached events grouped by tournament.
    """
    lines: list[str] = [f"Pre-match cache refreshed: {len(events)} events"]
    if events:
        lines.append(format_events_by_tournament(events))
    return "\n".join(lines)
