"""Domain models for market snapshots and comparisons."""

import json
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

JSON_SEPARATORS: tuple[str, str] = (",", ":")


class SnapshotPhase(StrEnum):
    """Market snapshot phases for a tracked event."""

    PREMATCH_60 = "PREMATCH_60"
    PREMATCH_15 = "PREMATCH_15"
    PREMATCH_1 = "PREMATCH_1"
    LIVE = "LIVE"

    @property
    def is_prematch(self) -> bool:
        """True for any prematch phase."""
        return self in (
            SnapshotPhase.PREMATCH_60,
            SnapshotPhase.PREMATCH_15,
            SnapshotPhase.PREMATCH_1,
        )


class Selection(BaseModel):
    """One selectable outcome within a market row (e.g., '1', 'X', '2')."""

    price_id: str
    name: str
    type_id: str
    price: float
    suspended: bool


class MarketRow(BaseModel):
    """One row of a market — corresponds to a single handicap line when present."""

    row_id: str
    handicap: str | None
    selections: list[Selection]


class Market(BaseModel):
    """A market type instance with all its rows and selections."""

    market_type_id: str
    market_type_name: str
    priority: int
    rows: list[MarketRow]


class DroppedMarketDetail(BaseModel):
    """A market that was in prematch but not in live."""

    market_type_id: str
    market_type_name: str


class AddedMarketDetail(BaseModel):
    """A market that appeared in live but not in prematch."""

    market_type_id: str
    market_type_name: str


class OddsShiftDetail(BaseModel):
    """A per-selection odds shift between prematch and live."""

    market_type_name: str
    selection_name: str
    selection_type_id: str
    handicap: str | None
    prematch_price: float
    live_price: float
    shift_pct: float


class ComparisonDetails(BaseModel):
    """Structured details of a market comparison."""

    dropped: list[DroppedMarketDetail]
    added: list[AddedMarketDetail]
    odds_shifts: list[OddsShiftDetail]


class MarketSnapshot(BaseModel):
    """A snapshot of all markets for an event at a given phase."""

    id: int | None = None
    event_id: int
    phase: SnapshotPhase
    taken_at: datetime
    markets: list[Market]
    total_market_count: int
    total_selection_count: int
    suspended_count: int

    @property
    def markets_json(self) -> str:
        """Serialize markets to JSON for DB storage."""
        return json.dumps([m.model_dump() for m in self.markets], separators=JSON_SEPARATORS)

    @staticmethod
    def markets_from_json(json_str: str) -> list[Market]:
        """Deserialize markets from JSON string."""
        data = json.loads(json_str)
        return [Market(**item) for item in data]


class MarketComparison(BaseModel):
    """Result of diffing a prematch snapshot against a live snapshot."""

    id: int | None = None
    event_id: int
    compared_at: datetime
    prematch_phase: SnapshotPhase
    markets_added: int
    markets_dropped: int
    markets_kept: int
    retention_pct: float
    dropped_key_markets: list[str]
    max_odds_shift_pct: float
    triggered_alert: bool
    details: ComparisonDetails
