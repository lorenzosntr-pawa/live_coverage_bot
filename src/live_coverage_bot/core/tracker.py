"""Alert tracker for duplicate alert prevention."""


class AlertTracker:
    """Tracks alerted event IDs and missing counts for confirmation.

    Uses in-memory sets for O(1) lookup. Events naturally expire when
    they leave the "live" state and stop appearing in API responses.

    Missing counts track consecutive checks an event has been missing,
    enabling confirmation delays to reduce false positive alerts.
    """

    def __init__(self) -> None:
        """Initialize the tracker with empty sets."""
        self._alerted_ids: set[str] = set()
        self._missing_counts: dict[str, int] = {}

    def has_been_alerted(self, event_id: str) -> bool:
        """Check if an event has already been alerted.

        Args:
            event_id: The event ID to check.

        Returns:
            True if the event has been alerted, False otherwise.
        """
        return event_id in self._alerted_ids

    def mark_alerted(self, event_id: str) -> None:
        """Mark an event as alerted.

        Args:
            event_id: The event ID to mark as alerted.
        """
        self._alerted_ids.add(event_id)

    def clear(self) -> None:
        """Clear all tracked event IDs.

        Useful for testing or resetting state.
        """
        self._alerted_ids.clear()

    def get_alerted_count(self) -> int:
        """Get the count of alerted events.

        Returns:
            Number of events that have been alerted.
        """
        return len(self._alerted_ids)

    def record_missing(self, event_id: str) -> int:
        """Record that an event was missing and return the consecutive count.

        Increments the missing count for the event (or sets to 1 if new).

        Args:
            event_id: The event ID that was found missing.

        Returns:
            The new consecutive missing count.
        """
        self._missing_counts[event_id] = self._missing_counts.get(event_id, 0) + 1
        return self._missing_counts[event_id]

    def clear_not_missing(self, still_missing_ids: set[str]) -> None:
        """Clear missing counts for events that reappeared.

        Removes any event from missing counts that is NOT in still_missing_ids.
        This resets tracking for events that have reappeared on BetPawa.

        Args:
            still_missing_ids: Set of event IDs that are currently missing.
        """
        to_remove = [eid for eid in self._missing_counts if eid not in still_missing_ids]
        for eid in to_remove:
            del self._missing_counts[eid]
