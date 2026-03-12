"""Alert tracker for duplicate alert prevention."""


class AlertTracker:
    """Tracks alerted event IDs to prevent duplicate notifications.

    Uses an in-memory set for O(1) lookup. Events naturally expire when
    they leave the "live" state and stop appearing in API responses.
    """

    def __init__(self) -> None:
        """Initialize the tracker with an empty set."""
        self._alerted_ids: set[str] = set()

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
