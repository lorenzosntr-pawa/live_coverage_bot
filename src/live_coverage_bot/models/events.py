"""Domain models for event lifecycle tracking."""

import json
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from live_coverage_bot.clients.models import ProviderID
from live_coverage_bot.models.markets import JSON_SEPARATORS


class EventStatus(StrEnum):
    """Event lifecycle states."""

    PREMATCH = "PREMATCH"
    LIVE = "LIVE"
    LATE = "LATE"
    NEVER_LIVE = "NEVER_LIVE"
    REMOVED = "REMOVED"
    UNMONITORED = "UNMONITORED"

    @property
    def is_terminal(self) -> bool:
        """Whether this status represents a final state (no further transitions)."""
        return self in (EventStatus.LIVE, EventStatus.NEVER_LIVE, EventStatus.UNMONITORED)


class TrackedEvent(BaseModel):
    """An event being tracked through its prematch-to-live lifecycle."""

    id: int | None = None
    betpawa_event_id: str
    home_team: str
    away_team: str
    competition: str
    country: str | None = None
    scheduled_kickoff: datetime
    status: EventStatus
    provider_ids: list[ProviderID]
    first_seen_prematch: datetime
    first_seen_live: datetime | None = None
    transition_delay_sec: int | None = None
    slack_message_ts: str | None = None
    removed_at: datetime | None = None
    pre_removal_market_count: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def provider_ids_json(self) -> str:
        """Serialize provider_ids to JSON string for DB storage."""
        return json.dumps(
            [{"type": pid.type.value, "id": pid.id} for pid in self.provider_ids],
            separators=JSON_SEPARATORS,
        )

    @staticmethod
    def provider_ids_from_json(json_str: str) -> list[ProviderID]:
        """Deserialize provider_ids from JSON string."""
        data = json.loads(json_str)
        return [ProviderID(type=item["type"], id=item["id"]) for item in data]


class StateChange(BaseModel):
    """Record of an event state transition."""

    id: int | None = None
    event_id: int
    old_status: EventStatus
    new_status: EventStatus
    changed_at: datetime
    details: str | None = None


class TransitionResult(BaseModel):
    """Result of a single event state transition."""

    event: TrackedEvent
    old_status: EventStatus
    new_status: EventStatus
    delay_sec: int | None = None
    details: str = ""


class RemovedResult(BaseModel):
    """Result of detecting a removed event."""

    event: TrackedEvent
    old_status: EventStatus
    details: str = ""
