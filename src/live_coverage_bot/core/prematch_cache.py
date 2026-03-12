"""Pre-match event cache for filtering false positive alerts."""

from datetime import UTC, datetime

from live_coverage_bot.clients.models import ProviderID, UpcomingEvent


class PreMatchCache:
    """Cache of BetPawa pre-match provider IDs.

    Stores provider IDs from BetPawa's pre-match offerings.
    Used to filter alerts - only alert for matches BetPawa intended to offer.
    Also stores full event data for human-readable logging.
    """

    def __init__(self) -> None:
        """Initialize empty cache."""
        # Map of provider_id -> start_time for fast lookup
        self._cache: dict[str, datetime] = {}
        # Full event list for logging
        self._events: list[UpcomingEvent] = []
        self._last_refresh: datetime | None = None

    def update(self, events: list[UpcomingEvent]) -> None:
        """Replace cache contents with new data.

        Args:
            events: List of UpcomingEvent with full details and provider IDs.
        """
        # Store events for logging
        self._events = events.copy()

        # Build lookup dict from provider IDs
        self._cache = {}
        for event in events:
            for pid in event.provider_ids:
                key = f"{pid.type.value}:{pid.id}"
                self._cache[key] = event.start_time

        self._last_refresh = datetime.now(tz=UTC)

    def was_offered_prematch(self, provider_ids: list[ProviderID]) -> bool:
        """Check if ANY of the provider IDs were in pre-match.

        Args:
            provider_ids: List of provider IDs to check.

        Returns:
            True if at least one provider ID matches cache.
        """
        for pid in provider_ids:
            key = f"{pid.type.value}:{pid.id}"
            if key in self._cache:
                return True
        return False

    def needs_refresh(self, interval_seconds: int = 300) -> bool:
        """Check if cache needs refreshing.

        Args:
            interval_seconds: Refresh interval (default 5 minutes).

        Returns:
            True if cache has never been refreshed or interval has passed.
        """
        if self._last_refresh is None:
            return True
        age = (datetime.now(tz=UTC) - self._last_refresh).total_seconds()
        return age >= interval_seconds

    @property
    def events(self) -> list[UpcomingEvent]:
        """Cached upcoming events for logging."""
        return self._events

    @property
    def size(self) -> int:
        """Number of provider IDs in cache."""
        return len(self._cache)
