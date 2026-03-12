"""Event matching engine for cross-platform comparison."""

from live_coverage_bot.clients.models import LiveEvent, ProviderID


class EventMatcher:
    """Compares events across platforms using provider IDs."""

    def find_missing_events(
        self,
        sportybet_events: list[LiveEvent],
        betpawa_events: list[LiveEvent],
    ) -> list[LiveEvent]:
        """Find SportyBet events missing from BetPawa.

        Uses provider ID matching:
        - Build set of all (type, id) tuples from BetPawa events
        - For each SportyBet event, check if ANY provider ID exists in BetPawa set
        - Return events where NO provider IDs match

        Returns: List of LiveEvent objects from SportyBet that are missing on BetPawa
        """
        # Build set of (type, id) tuples from all BetPawa events' provider_ids
        betpawa_ids: set[tuple[str, str]] = set()
        for event in betpawa_events:
            for provider_id in event.provider_ids:
                betpawa_ids.add((provider_id.type, provider_id.id))

        # Find SportyBet events with no matching provider IDs
        missing: list[LiveEvent] = []
        for event in sportybet_events:
            event_ids = event.provider_ids
            # Check if ANY provider ID matches
            has_match = any(
                (pid.type, pid.id) in betpawa_ids for pid in event_ids
            )
            if not has_match:
                missing.append(event)

        return missing
