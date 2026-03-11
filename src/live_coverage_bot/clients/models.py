"""Data models for API client responses."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, computed_field


class ProviderType(StrEnum):
    """Provider types for live event data."""

    SPORTRADAR = "SPORTRADAR"
    GENIUSSPORTS = "GENIUSSPORTS"


class ProviderID(BaseModel):
    """Provider identifier for cross-platform matching."""

    type: ProviderType
    id: str


class LiveEvent(BaseModel):
    """Live event data from SportyBet."""

    event_id: str  # Raw SportyBet event ID like "sr:match:12345"
    home_team: str
    away_team: str
    competition_id: str
    competition_name: str
    minute: str | None = None  # Match minute if live
    home_score: int | None = None
    away_score: int | None = None
    start_time: datetime

    @computed_field
    @property
    def provider_ids(self) -> list[ProviderID]:
        """Extract provider IDs from event_id.

        SportyBet event ID format:
        - "sr:match:12345" -> SportRadar ID 12345
        - "sr:match:1111111112345" -> GeniusSports ID 12345 (8 ones prefix)
        """
        return self._extract_provider_ids()

    def _extract_provider_ids(self) -> list[ProviderID]:
        """Parse event_id to extract provider information."""
        ids: list[ProviderID] = []

        if not self.event_id.startswith("sr:match:"):
            return ids

        # Extract numeric portion after "sr:match:"
        numeric_part = self.event_id[9:]  # After "sr:match:"

        # GeniusSports prefix: 8 ones (11111111)
        if numeric_part.startswith("11111111"):
            provider_id = numeric_part[8:]  # Remove the 8 ones prefix
            if provider_id:
                ids.append(ProviderID(type=ProviderType.GENIUSSPORTS, id=provider_id))
        else:
            # SportRadar: just the numeric ID
            if numeric_part:
                ids.append(ProviderID(type=ProviderType.SPORTRADAR, id=numeric_part))

        return ids
