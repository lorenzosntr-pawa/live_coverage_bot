# BetPawa Market Comparison — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add market-level comparison on top of the v2 prematch-to-live monitor: take scheduled snapshots of BetPawa markets at 60/15/1 min before kickoff and on LIVE transition, diff them, store in SQLite, alert on noteworthy changes, and produce aggregate + per-event CSV reports.

**Architecture:** Inline extension of the existing 30s polling loop. Each cycle, the snapshotter identifies events with snapshot windows due now, fetches `GET /events/{id}` in parallel via `asyncio.gather`, stores raw snapshots as JSON blobs alongside denormalized count fields. When a LIVE snapshot is stored, the comparator diffs it against the latest prematch snapshot, writes a comparison row, and routes Slack output (thread reply on existing alert, or new standalone message for notable happy-path events, or silent).

**Tech Stack:** Python 3.11+, httpx (async HTTP), pydantic (models/config), aiosqlite (SQLite), Slack Web API (chat.postMessage / chat.update), stdlib csv / json.

---

## File Structure

```
src/live_coverage_bot/
├── models/
│   └── markets.py                   # NEW: SnapshotPhase, Selection, MarketRow, Market, MarketSnapshot, MarketComparison
├── db/
│   ├── connection.py                # MODIFY: extend SCHEMA_SQL with 2 new tables + indexes
│   └── market_repository.py         # NEW: CRUD for snapshots & comparisons
├── clients/
│   ├── betpawa.py                   # MODIFY: add get_event_markets() method
│   └── slack.py                     # MODIFY: add post_market_recap() + post_market_anomaly_alert()
├── config/
│   └── models.py                    # MODIFY: add MarketsConfig, MarketSnapshotWindowsConfig, MarketAlertThresholdsConfig
├── core/
│   ├── loop.py                      # MODIFY: wire market snapshotter + comparator into cycle
│   ├── market_snapshotter.py        # NEW: decides when/what to snapshot, fetches, stores
│   ├── market_comparator.py         # NEW: diff logic, writes comparison, returns alert decision
│   └── market_reporter.py           # NEW: per-event CSV + market summary block for weekly report

tests/
├── test_markets_models.py           # NEW
├── test_market_repository.py        # NEW
├── test_market_snapshotter.py       # NEW
├── test_market_comparator.py        # NEW
├── test_market_reporter.py          # NEW
├── test_slack.py                    # MODIFY: add tests for new Slack methods
├── test_config.py                   # MODIFY: add tests for MarketsConfig
└── test_loop.py                     # MODIFY: integration test for market snapshotter invocation

examples_api_markets/                # existing — used as fixtures
└── prematch_response_34210635.json
└── live_response_34397693.json
```

---

### Task 1: Market Domain Models

**Files:**
- Create: `src/live_coverage_bot/models/markets.py`
- Modify: `src/live_coverage_bot/models/__init__.py`
- Create: `tests/test_markets_models.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_markets_models.py`:

```python
"""Tests for market domain models."""

from datetime import UTC, datetime

from live_coverage_bot.models.markets import (
    Market,
    MarketComparison,
    MarketRow,
    MarketSnapshot,
    Selection,
    SnapshotPhase,
)


class TestSnapshotPhase:
    def test_phases_exist(self):
        assert SnapshotPhase.PREMATCH_60 == "PREMATCH_60"
        assert SnapshotPhase.PREMATCH_15 == "PREMATCH_15"
        assert SnapshotPhase.PREMATCH_1 == "PREMATCH_1"
        assert SnapshotPhase.LIVE == "LIVE"

    def test_is_prematch(self):
        assert SnapshotPhase.PREMATCH_60.is_prematch
        assert SnapshotPhase.PREMATCH_15.is_prematch
        assert SnapshotPhase.PREMATCH_1.is_prematch
        assert not SnapshotPhase.LIVE.is_prematch


class TestMarketModels:
    def test_selection_model(self):
        s = Selection(
            price_id="1430463477", name="1", type_id="3744",
            price=1.94, suspended=False,
        )
        assert s.price == 1.94
        assert not s.suspended

    def test_market_row_with_handicap(self):
        row = MarketRow(
            row_id="368398521", handicap="2.5",
            selections=[
                Selection(price_id="x", name="Over", type_id="5001", price=1.85, suspended=False),
                Selection(price_id="y", name="Under", type_id="5002", price=1.95, suspended=False),
            ],
        )
        assert row.handicap == "2.5"
        assert len(row.selections) == 2

    def test_market_model(self):
        m = Market(
            market_type_id="3743", market_type_name="1X2 - FT",
            priority=1, rows=[],
        )
        assert m.market_type_id == "3743"
        assert m.priority == 1


class TestMarketSnapshot:
    def test_snapshot_fields(self):
        snap = MarketSnapshot(
            event_id=5,
            phase=SnapshotPhase.PREMATCH_1,
            taken_at=datetime(2026, 4, 15, 14, 59, tzinfo=UTC),
            markets=[],
            total_market_count=87,
            total_selection_count=312,
            suspended_count=4,
        )
        assert snap.event_id == 5
        assert snap.phase == SnapshotPhase.PREMATCH_1
        assert snap.total_market_count == 87

    def test_markets_json_serialization(self):
        snap = MarketSnapshot(
            event_id=5,
            phase=SnapshotPhase.LIVE,
            taken_at=datetime(2026, 4, 15, 15, 1, tzinfo=UTC),
            markets=[
                Market(
                    market_type_id="3743", market_type_name="1X2 - FT", priority=1,
                    rows=[MarketRow(
                        row_id="r1", handicap=None,
                        selections=[Selection(price_id="p1", name="1", type_id="3744",
                                              price=2.10, suspended=False)],
                    )],
                )
            ],
            total_market_count=1,
            total_selection_count=1,
            suspended_count=0,
        )
        json_str = snap.markets_json
        assert '"market_type_id":"3743"' in json_str or '"market_type_id": "3743"' in json_str
        restored = MarketSnapshot.markets_from_json(json_str)
        assert len(restored) == 1
        assert restored[0].market_type_id == "3743"


class TestMarketComparison:
    def test_comparison_fields(self):
        c = MarketComparison(
            event_id=5,
            compared_at=datetime(2026, 4, 15, 15, 1, tzinfo=UTC),
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0,
            markets_dropped=53,
            markets_kept=34,
            retention_pct=39.1,
            dropped_key_markets=["Both Teams To Score - FT"],
            max_odds_shift_pct=16.7,
            triggered_alert=True,
            details={"dropped": ["Corner markets"], "added": [], "odds_shifts": []},
        )
        assert c.retention_pct == 39.1
        assert c.triggered_alert
        assert "Both Teams To Score - FT" in c.dropped_key_markets
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_markets_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'live_coverage_bot.models.markets'`

- [ ] **Step 3: Implement models**

Create `src/live_coverage_bot/models/markets.py`:

```python
"""Domain models for market snapshots and comparisons."""

import json
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


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
        return json.dumps([m.model_dump() for m in self.markets], separators=(",", ":"))

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
    details: dict
```

- [ ] **Step 4: Export from models/__init__.py**

Replace the FULL contents of `src/live_coverage_bot/models/__init__.py`:

```python
"""Domain models for event lifecycle and market tracking."""

from live_coverage_bot.models.events import EventStatus, StateChange, TrackedEvent
from live_coverage_bot.models.markets import (
    Market,
    MarketComparison,
    MarketRow,
    MarketSnapshot,
    Selection,
    SnapshotPhase,
)

__all__ = [
    "EventStatus",
    "Market",
    "MarketComparison",
    "MarketRow",
    "MarketSnapshot",
    "Selection",
    "SnapshotPhase",
    "StateChange",
    "TrackedEvent",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_markets_models.py -v`
Expected: All 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/models/markets.py src/live_coverage_bot/models/__init__.py tests/test_markets_models.py
git commit -m "feat: market domain models (Snapshot, Market, Selection, Comparison)"
```

---

### Task 2: Markets Config Schema

**Files:**
- Modify: `src/live_coverage_bot/config/models.py`
- Modify: `src/live_coverage_bot/config/__init__.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_config.py`:

```python


class TestMarketsConfig:
    def test_defaults(self):
        from live_coverage_bot.config.models import (
            MarketAlertThresholdsConfig,
            MarketSnapshotWindowsConfig,
            MarketsConfig,
        )

        config = MarketsConfig()
        assert config.enabled is True
        assert isinstance(config.snapshot_windows, MarketSnapshotWindowsConfig)
        assert config.snapshot_windows.prematch_60_min_before == [60, 55]
        assert config.snapshot_windows.prematch_15_min_before == [15, 10]
        assert config.snapshot_windows.prematch_1_min_before == [1, 0]
        assert isinstance(config.alert_thresholds, MarketAlertThresholdsConfig)
        assert config.alert_thresholds.retention_below_pct == 50
        assert config.alert_thresholds.any_key_market_dropped is True
        assert config.alert_thresholds.max_odds_shift_pct == 30
        assert "1X2 - FT" in config.key_markets
        assert "Total Score Over/Under - FT" in config.key_markets

    def test_settings_includes_markets_section(self):
        from live_coverage_bot.config.models import Settings, SlackConfig

        settings = Settings(
            slack=SlackConfig(bot_token="xoxb-test", channel_id="C123"),
            _env_file=None,
        )
        assert settings.markets.enabled is True
        assert settings.markets.alert_thresholds.retention_below_pct == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py::TestMarketsConfig -v`
Expected: FAIL — `ImportError: cannot import name 'MarketsConfig'`

- [ ] **Step 3: Implement MarketsConfig**

Append to `src/live_coverage_bot/config/models.py` (before the `Settings` class):

```python
class MarketSnapshotWindowsConfig(BaseModel):
    """Snapshot window minute ranges (relative to kickoff)."""

    prematch_60_min_before: list[int] = [60, 55]
    prematch_15_min_before: list[int] = [15, 10]
    prematch_1_min_before: list[int] = [1, 0]


class MarketAlertThresholdsConfig(BaseModel):
    """Thresholds for firing market comparison alerts."""

    retention_below_pct: float = 50
    any_key_market_dropped: bool = True
    max_odds_shift_pct: float = 30


class MarketsConfig(BaseModel):
    """Market comparison feature configuration."""

    enabled: bool = True
    snapshot_windows: MarketSnapshotWindowsConfig = MarketSnapshotWindowsConfig()
    alert_thresholds: MarketAlertThresholdsConfig = MarketAlertThresholdsConfig()
    key_markets: list[str] = [
        "1X2 - FT",
        "Total Score Over/Under - FT",
        "Both Teams To Score - FT",
        "Double Chance - FT",
        "1X2 - 1H",
    ]
```

Then modify the `Settings` class — add a `markets` field. Find the existing `Settings` class and change it to:

```python
class Settings(BaseSettings):
    """Application settings with environment variable support.

    Environment variables use LCB_ prefix and __ for nested values.
    Example: LCB_SLACK__BOT_TOKEN=xoxb-..., LCB_SLACK__CHANNEL_ID=C123
    """

    polling: PollingConfig = PollingConfig()
    thresholds: ThresholdConfig = ThresholdConfig()
    betpawa: BetPawaConfig = BetPawaConfig()
    slack: SlackConfig
    database: DatabaseConfig = DatabaseConfig()
    reporting: ReportingConfig = ReportingConfig()
    markets: MarketsConfig = MarketsConfig()

    model_config = SettingsConfigDict(
        env_prefix="LCB_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )
```

- [ ] **Step 4: Update config/__init__.py exports**

Replace the FULL contents of `src/live_coverage_bot/config/__init__.py`:

```python
"""Configuration module for Live Coverage Bot."""

from .loader import load_config
from .models import (
    BetPawaConfig,
    DatabaseConfig,
    MarketAlertThresholdsConfig,
    MarketsConfig,
    MarketSnapshotWindowsConfig,
    PollingConfig,
    ReportingConfig,
    Settings,
    SlackConfig,
    ThresholdConfig,
)

__all__ = [
    "BetPawaConfig",
    "DatabaseConfig",
    "MarketAlertThresholdsConfig",
    "MarketsConfig",
    "MarketSnapshotWindowsConfig",
    "PollingConfig",
    "ReportingConfig",
    "Settings",
    "SlackConfig",
    "ThresholdConfig",
    "load_config",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: All existing + 2 new tests PASS.

- [ ] **Step 6: Update config.yaml and config.example.yaml**

Append to `config.yaml` (and `config.example.yaml`):

```yaml
markets:
  enabled: true
  snapshot_windows:
    prematch_60_min_before: [60, 55]
    prematch_15_min_before: [15, 10]
    prematch_1_min_before: [1, 0]
  alert_thresholds:
    retention_below_pct: 50
    any_key_market_dropped: true
    max_odds_shift_pct: 30
  key_markets:
    - "1X2 - FT"
    - "Total Score Over/Under - FT"
    - "Both Teams To Score - FT"
    - "Double Chance - FT"
    - "1X2 - 1H"
```

Note: `config.yaml` is gitignored — only commit `config.example.yaml`.

- [ ] **Step 7: Commit**

```bash
git add src/live_coverage_bot/config/ tests/test_config.py config.example.yaml
git commit -m "feat: markets config schema with snapshot windows, thresholds, key markets"
```

---

### Task 3: BetPawa Event Detail Endpoint

**Files:**
- Modify: `src/live_coverage_bot/clients/betpawa.py`
- Create: `tests/test_betpawa_markets.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_betpawa_markets.py`:

```python
"""Tests for BetPawa event detail fetch with markets."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from live_coverage_bot.clients.betpawa import BetPawaClient, BetPawaError
from live_coverage_bot.config.models import BetPawaConfig
from live_coverage_bot.models.markets import Market


FIXTURE_DIR = Path(__file__).parent.parent / "examples_api_markets"


@pytest.fixture
def betpawa_client():
    return BetPawaClient(BetPawaConfig())


@pytest.fixture
def prematch_response_data():
    with open(FIXTURE_DIR / "prematch_response_34210635.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def live_response_data():
    with open(FIXTURE_DIR / "live_response_34397693.json", encoding="utf-8") as f:
        return json.load(f)


class TestGetEventMarkets:
    async def test_parses_prematch_response(self, betpawa_client, prematch_response_data):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=prematch_response_data)

        with pytest.MonkeyPatch().context() as mp:
            async def fake_get(*args, **kwargs):
                return mock_resp

            mp.setattr(betpawa_client._client, "get", fake_get)
            markets = await betpawa_client.get_event_markets("34210635")

        assert isinstance(markets, list)
        assert len(markets) > 0
        assert all(isinstance(m, Market) for m in markets)
        # 1X2 FT should be there
        mt_ids = {m.market_type_id for m in markets}
        assert "3743" in mt_ids
        # 1X2 market should have 3 selections (1, X, 2)
        ft_market = next(m for m in markets if m.market_type_id == "3743")
        assert len(ft_market.rows) == 1
        assert len(ft_market.rows[0].selections) == 3
        selection_names = {s.name for s in ft_market.rows[0].selections}
        assert selection_names == {"1", "X", "2"}

    async def test_parses_live_response(self, betpawa_client, live_response_data):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=live_response_data)

        with pytest.MonkeyPatch().context() as mp:
            async def fake_get(*args, **kwargs):
                return mock_resp

            mp.setattr(betpawa_client._client, "get", fake_get)
            markets = await betpawa_client.get_event_markets("34397693")

        assert len(markets) > 0
        # Live should be a smaller subset — but should still have 1X2
        mt_ids = {m.market_type_id for m in markets}
        assert "3743" in mt_ids

    async def test_raises_on_http_error(self, betpawa_client):
        import httpx
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "not found", request=MagicMock(), response=MagicMock(status_code=404)
            )
        )
        mock_resp.response = MagicMock(status_code=404)

        with pytest.MonkeyPatch().context() as mp:
            async def fake_get(*args, **kwargs):
                return mock_resp

            mp.setattr(betpawa_client._client, "get", fake_get)
            with pytest.raises(BetPawaError):
                await betpawa_client.get_event_markets("bad")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_betpawa_markets.py -v`
Expected: FAIL — `AttributeError: 'BetPawaClient' object has no attribute 'get_event_markets'`

- [ ] **Step 3: Implement `get_event_markets`**

Add this method to the `BetPawaClient` class in `src/live_coverage_bot/clients/betpawa.py` (before the existing `build_live_betpawa_id_set` helper):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_betpawa_markets.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/clients/betpawa.py tests/test_betpawa_markets.py
git commit -m "feat: BetPawaClient.get_event_markets() with full market parsing"
```

---

### Task 4: SQLite Schema Extension — Market Tables

**Files:**
- Modify: `src/live_coverage_bot/db/connection.py`

- [ ] **Step 1: Extend the SCHEMA_SQL constant**

Replace the `SCHEMA_SQL` constant in `src/live_coverage_bot/db/connection.py` with:

```python
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    betpawa_event_id TEXT UNIQUE NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    competition TEXT NOT NULL,
    country TEXT,
    scheduled_kickoff TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_ids TEXT NOT NULL,
    first_seen_prematch TEXT NOT NULL,
    first_seen_live TEXT,
    transition_delay_sec INTEGER,
    slack_message_ts TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_state_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    old_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    details TEXT
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    phase TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    markets_json TEXT NOT NULL,
    total_market_count INTEGER NOT NULL,
    total_selection_count INTEGER NOT NULL,
    suspended_count INTEGER NOT NULL,
    fetch_duration_ms INTEGER,
    UNIQUE (event_id, phase)
);

CREATE TABLE IF NOT EXISTS market_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    compared_at TEXT NOT NULL,
    prematch_phase TEXT NOT NULL,
    markets_added INTEGER NOT NULL,
    markets_dropped INTEGER NOT NULL,
    markets_kept INTEGER NOT NULL,
    retention_pct REAL NOT NULL,
    dropped_key_markets TEXT NOT NULL,
    max_odds_shift_pct REAL NOT NULL,
    triggered_alert INTEGER NOT NULL,
    details_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE INDEX IF NOT EXISTS idx_events_kickoff ON events(scheduled_kickoff);
CREATE INDEX IF NOT EXISTS idx_snapshots_event ON market_snapshots(event_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_taken_at ON market_snapshots(taken_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_phase ON market_snapshots(phase);
CREATE INDEX IF NOT EXISTS idx_comparisons_event ON market_comparisons(event_id);
CREATE INDEX IF NOT EXISTS idx_comparisons_compared_at ON market_comparisons(compared_at);
CREATE INDEX IF NOT EXISTS idx_comparisons_alert ON market_comparisons(triggered_alert);
"""
```

**Note:** `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` are safe to re-run against existing databases — they won't touch existing tables/data.

- [ ] **Step 2: Add quick sanity test for schema creation**

Append to `tests/test_db.py`:

```python


class TestMarketSchema:
    async def test_market_tables_exist(self, db):
        tables = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = {r["name"] for r in tables}
        assert "market_snapshots" in names
        assert "market_comparisons" in names

    async def test_market_indexes_exist(self, db):
        indexes = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
        names = {r["name"] for r in indexes}
        assert "idx_snapshots_event" in names
        assert "idx_snapshots_taken_at" in names
        assert "idx_snapshots_phase" in names
        assert "idx_comparisons_event" in names
        assert "idx_comparisons_compared_at" in names
        assert "idx_comparisons_alert" in names
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: All existing + 2 new tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/live_coverage_bot/db/connection.py tests/test_db.py
git commit -m "feat: SQLite schema for market_snapshots and market_comparisons tables"
```

---

### Task 5: Market Repository

**Files:**
- Create: `src/live_coverage_bot/db/market_repository.py`
- Modify: `src/live_coverage_bot/db/__init__.py`
- Create: `tests/test_market_repository.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_market_repository.py`:

```python
"""Tests for the market repository."""

import json
from datetime import UTC, datetime

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.models.markets import (
    Market,
    MarketComparison,
    MarketRow,
    MarketSnapshot,
    Selection,
    SnapshotPhase,
)


@pytest.fixture
def event_repo(db: Database) -> EventRepository:
    return EventRepository(db)


@pytest.fixture
def market_repo(db: Database) -> MarketRepository:
    return MarketRepository(db)


@pytest.fixture
async def event_id(event_repo) -> int:
    event = TrackedEvent(
        betpawa_event_id="99001",
        home_team="A", away_team="B",
        competition="X", country="Y",
        scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
        status=EventStatus.PREMATCH,
        provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
        first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
    )
    return await event_repo.insert_event(event)


def _sample_market() -> Market:
    return Market(
        market_type_id="3743",
        market_type_name="1X2 - FT",
        priority=1,
        rows=[MarketRow(
            row_id="r1", handicap=None,
            selections=[
                Selection(price_id="p1", name="1", type_id="3744", price=2.10, suspended=False),
                Selection(price_id="p2", name="X", type_id="3745", price=3.50, suspended=False),
                Selection(price_id="p3", name="2", type_id="3746", price=3.80, suspended=False),
            ],
        )],
    )


class TestSnapshotOps:
    async def test_insert_and_fetch_snapshot(self, market_repo, event_id):
        snap = MarketSnapshot(
            event_id=event_id,
            phase=SnapshotPhase.PREMATCH_60,
            taken_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
            markets=[_sample_market()],
            total_market_count=1,
            total_selection_count=3,
            suspended_count=0,
        )
        row_id = await market_repo.insert_snapshot(snap, fetch_duration_ms=120)
        assert row_id > 0

        latest_prematch = await market_repo.get_latest_prematch_snapshot(event_id)
        assert latest_prematch is not None
        assert latest_prematch.phase == SnapshotPhase.PREMATCH_60
        assert len(latest_prematch.markets) == 1
        assert latest_prematch.markets[0].market_type_id == "3743"

    async def test_latest_prematch_prefers_closest_phase(self, market_repo, event_id):
        # Insert 60 first, then 15 (which is closer to kickoff)
        base_markets = [_sample_market()]
        await market_repo.insert_snapshot(
            MarketSnapshot(
                event_id=event_id, phase=SnapshotPhase.PREMATCH_60,
                taken_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
                markets=base_markets, total_market_count=1,
                total_selection_count=3, suspended_count=0,
            ),
        )
        await market_repo.insert_snapshot(
            MarketSnapshot(
                event_id=event_id, phase=SnapshotPhase.PREMATCH_1,
                taken_at=datetime(2026, 4, 15, 14, 59, tzinfo=UTC),
                markets=base_markets, total_market_count=1,
                total_selection_count=3, suspended_count=0,
            ),
        )

        latest = await market_repo.get_latest_prematch_snapshot(event_id)
        assert latest is not None
        assert latest.phase == SnapshotPhase.PREMATCH_1

    async def test_phases_already_taken(self, market_repo, event_id):
        await market_repo.insert_snapshot(
            MarketSnapshot(
                event_id=event_id, phase=SnapshotPhase.PREMATCH_60,
                taken_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
                markets=[_sample_market()],
                total_market_count=1, total_selection_count=3, suspended_count=0,
            ),
        )
        taken = await market_repo.phases_taken_for_event(event_id)
        assert SnapshotPhase.PREMATCH_60 in taken
        assert SnapshotPhase.PREMATCH_1 not in taken

    async def test_unique_constraint_per_phase(self, market_repo, event_id):
        snap = MarketSnapshot(
            event_id=event_id, phase=SnapshotPhase.PREMATCH_60,
            taken_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
            markets=[_sample_market()],
            total_market_count=1, total_selection_count=3, suspended_count=0,
        )
        await market_repo.insert_snapshot(snap)
        with pytest.raises(Exception):
            await market_repo.insert_snapshot(snap)


class TestComparisonOps:
    async def test_insert_comparison(self, market_repo, event_id):
        cmp = MarketComparison(
            event_id=event_id,
            compared_at=datetime(2026, 4, 15, 15, 1, tzinfo=UTC),
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0,
            markets_dropped=53,
            markets_kept=34,
            retention_pct=39.1,
            dropped_key_markets=["Both Teams To Score - FT"],
            max_odds_shift_pct=16.7,
            triggered_alert=True,
            details={"dropped": ["Corners"], "added": [], "odds_shifts": []},
        )
        row_id = await market_repo.insert_comparison(cmp)
        assert row_id > 0

        fetched = await market_repo.get_comparison_for_event(event_id)
        assert fetched is not None
        assert fetched.retention_pct == 39.1
        assert fetched.triggered_alert
        assert "Both Teams To Score - FT" in fetched.dropped_key_markets

    async def test_get_comparisons_in_date_range(self, market_repo, event_id):
        cmp = MarketComparison(
            event_id=event_id,
            compared_at=datetime(2026, 4, 15, 15, 1, tzinfo=UTC),
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0, markets_dropped=10, markets_kept=5,
            retention_pct=33.3, dropped_key_markets=[],
            max_odds_shift_pct=5.0, triggered_alert=False,
            details={"dropped": [], "added": [], "odds_shifts": []},
        )
        await market_repo.insert_comparison(cmp)

        start = datetime(2026, 4, 14, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        rows = await market_repo.get_comparisons_in_date_range(start, end)
        assert len(rows) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_market_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'live_coverage_bot.db.market_repository'`

- [ ] **Step 3: Implement MarketRepository**

Create `src/live_coverage_bot/db/market_repository.py`:

```python
"""Market snapshot and comparison persistence."""

import json
from datetime import datetime

from live_coverage_bot.db.connection import Database
from live_coverage_bot.models.markets import (
    MarketComparison,
    MarketSnapshot,
    SnapshotPhase,
)


# Prematch phases ordered closest-to-kickoff first for latest-lookup.
PREMATCH_PHASES_CLOSEST_FIRST = (
    SnapshotPhase.PREMATCH_1,
    SnapshotPhase.PREMATCH_15,
    SnapshotPhase.PREMATCH_60,
)


class MarketRepository:
    """Persistence for market snapshots and comparisons."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_snapshot(
        self, snapshot: MarketSnapshot, fetch_duration_ms: int | None = None
    ) -> int:
        cursor = await self._db.execute(
            """INSERT INTO market_snapshots
            (event_id, phase, taken_at, markets_json, total_market_count,
             total_selection_count, suspended_count, fetch_duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot.event_id,
                snapshot.phase.value,
                snapshot.taken_at.isoformat(),
                snapshot.markets_json,
                snapshot.total_market_count,
                snapshot.total_selection_count,
                snapshot.suspended_count,
                fetch_duration_ms,
            ),
        )
        return cursor.lastrowid

    async def phases_taken_for_event(self, event_id: int) -> set[SnapshotPhase]:
        rows = await self._db.fetch_all(
            "SELECT phase FROM market_snapshots WHERE event_id = ?",
            (event_id,),
        )
        return {SnapshotPhase(r["phase"]) for r in rows}

    async def get_latest_prematch_snapshot(
        self, event_id: int
    ) -> MarketSnapshot | None:
        """Return the prematch snapshot closest to kickoff (PREMATCH_1 preferred)."""
        for phase in PREMATCH_PHASES_CLOSEST_FIRST:
            row = await self._db.fetch_one(
                "SELECT * FROM market_snapshots WHERE event_id = ? AND phase = ?",
                (event_id, phase.value),
            )
            if row is not None:
                return self._row_to_snapshot(row)
        return None

    async def get_snapshots_for_event(self, event_id: int) -> list[MarketSnapshot]:
        rows = await self._db.fetch_all(
            "SELECT * FROM market_snapshots WHERE event_id = ? ORDER BY taken_at",
            (event_id,),
        )
        return [self._row_to_snapshot(r) for r in rows]

    async def insert_comparison(self, cmp: MarketComparison) -> int:
        cursor = await self._db.execute(
            """INSERT INTO market_comparisons
            (event_id, compared_at, prematch_phase, markets_added, markets_dropped,
             markets_kept, retention_pct, dropped_key_markets, max_odds_shift_pct,
             triggered_alert, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cmp.event_id,
                cmp.compared_at.isoformat(),
                cmp.prematch_phase.value,
                cmp.markets_added,
                cmp.markets_dropped,
                cmp.markets_kept,
                cmp.retention_pct,
                json.dumps(cmp.dropped_key_markets),
                cmp.max_odds_shift_pct,
                1 if cmp.triggered_alert else 0,
                json.dumps(cmp.details, separators=(",", ":")),
            ),
        )
        return cursor.lastrowid

    async def get_comparison_for_event(self, event_id: int) -> MarketComparison | None:
        row = await self._db.fetch_one(
            "SELECT * FROM market_comparisons WHERE event_id = ?",
            (event_id,),
        )
        if row is None:
            return None
        return self._row_to_comparison(row)

    async def get_comparisons_in_date_range(
        self, start: datetime, end: datetime
    ) -> list[MarketComparison]:
        rows = await self._db.fetch_all(
            "SELECT * FROM market_comparisons WHERE compared_at >= ? AND compared_at <= ?",
            (start.isoformat(), end.isoformat()),
        )
        return [self._row_to_comparison(r) for r in rows]

    def _row_to_snapshot(self, row: dict) -> MarketSnapshot:
        return MarketSnapshot(
            id=row["id"],
            event_id=row["event_id"],
            phase=SnapshotPhase(row["phase"]),
            taken_at=datetime.fromisoformat(row["taken_at"]),
            markets=MarketSnapshot.markets_from_json(row["markets_json"]),
            total_market_count=row["total_market_count"],
            total_selection_count=row["total_selection_count"],
            suspended_count=row["suspended_count"],
        )

    def _row_to_comparison(self, row: dict) -> MarketComparison:
        return MarketComparison(
            id=row["id"],
            event_id=row["event_id"],
            compared_at=datetime.fromisoformat(row["compared_at"]),
            prematch_phase=SnapshotPhase(row["prematch_phase"]),
            markets_added=row["markets_added"],
            markets_dropped=row["markets_dropped"],
            markets_kept=row["markets_kept"],
            retention_pct=row["retention_pct"],
            dropped_key_markets=json.loads(row["dropped_key_markets"]),
            max_odds_shift_pct=row["max_odds_shift_pct"],
            triggered_alert=bool(row["triggered_alert"]),
            details=json.loads(row["details_json"]),
        )
```

- [ ] **Step 4: Update db/__init__.py exports**

Replace the FULL contents of `src/live_coverage_bot/db/__init__.py`:

```python
"""Database layer for event persistence."""

from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository

__all__ = [
    "Database",
    "EventRepository",
    "MarketRepository",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_market_repository.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/db/market_repository.py src/live_coverage_bot/db/__init__.py tests/test_market_repository.py
git commit -m "feat: MarketRepository for snapshot and comparison persistence"
```

---

### Task 6: Market Comparator

**Files:**
- Create: `src/live_coverage_bot/core/market_comparator.py`
- Create: `tests/test_market_comparator.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_market_comparator.py`:

```python
"""Tests for market comparator (diff + alert decision)."""

from datetime import UTC, datetime

from live_coverage_bot.config.models import (
    MarketAlertThresholdsConfig,
    MarketsConfig,
)
from live_coverage_bot.core.market_comparator import compare_snapshots
from live_coverage_bot.models.markets import (
    Market,
    MarketRow,
    MarketSnapshot,
    Selection,
    SnapshotPhase,
)


def _snap(event_id: int, phase: SnapshotPhase, markets: list[Market]) -> MarketSnapshot:
    return MarketSnapshot(
        event_id=event_id,
        phase=phase,
        taken_at=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
        markets=markets,
        total_market_count=len(markets),
        total_selection_count=sum(len(r.selections) for m in markets for r in m.rows),
        suspended_count=sum(
            1 for m in markets for r in m.rows for s in r.selections if s.suspended
        ),
    )


def _market_1x2(home_odds=2.0, draw_odds=3.3, away_odds=4.0):
    return Market(
        market_type_id="3743", market_type_name="1X2 - FT", priority=1,
        rows=[MarketRow(row_id="r1", handicap=None, selections=[
            Selection(price_id="p1", name="1", type_id="3744", price=home_odds, suspended=False),
            Selection(price_id="p2", name="X", type_id="3745", price=draw_odds, suspended=False),
            Selection(price_id="p3", name="2", type_id="3746", price=away_odds, suspended=False),
        ])],
    )


def _market_btts():
    return Market(
        market_type_id="3795", market_type_name="Both Teams To Score - FT", priority=10,
        rows=[MarketRow(row_id="r2", handicap=None, selections=[
            Selection(price_id="p4", name="Yes", type_id="3796", price=1.85, suspended=False),
            Selection(price_id="p5", name="No", type_id="3797", price=1.95, suspended=False),
        ])],
    )


def _market_ou(handicap="2.5", over=1.85, under=1.95):
    return Market(
        market_type_id="5000", market_type_name="Total Score Over/Under - FT", priority=5,
        rows=[MarketRow(row_id="r3", handicap=handicap, selections=[
            Selection(price_id=f"pou_{handicap}_o", name="Over", type_id="5001",
                     price=over, suspended=False),
            Selection(price_id=f"pou_{handicap}_u", name="Under", type_id="5002",
                     price=under, suspended=False),
        ])],
    )


def _default_config() -> MarketsConfig:
    return MarketsConfig(
        alert_thresholds=MarketAlertThresholdsConfig(
            retention_below_pct=50,
            any_key_market_dropped=True,
            max_odds_shift_pct=30,
        ),
        key_markets=[
            "1X2 - FT",
            "Total Score Over/Under - FT",
            "Both Teams To Score - FT",
        ],
    )


class TestCompareSnapshots:
    def test_full_retention_no_alert(self):
        markets = [_market_1x2()]
        pre = _snap(1, SnapshotPhase.PREMATCH_1, markets)
        live = _snap(1, SnapshotPhase.LIVE, markets)
        cfg = _default_config()

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert cmp.markets_dropped == 0
        assert cmp.markets_added == 0
        assert cmp.markets_kept == 1
        assert cmp.retention_pct == 100.0
        assert not cmp.triggered_alert

    def test_market_dropped(self):
        pre = _snap(1, SnapshotPhase.PREMATCH_1, [_market_1x2(), _market_btts()])
        live = _snap(1, SnapshotPhase.LIVE, [_market_1x2()])
        cfg = _default_config()

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert cmp.markets_dropped == 1
        assert cmp.markets_kept == 1
        assert cmp.retention_pct == 50.0
        # BTTS is a key market and was dropped → alert
        assert "Both Teams To Score - FT" in cmp.dropped_key_markets
        assert cmp.triggered_alert

    def test_retention_below_threshold(self):
        pre_markets = [_market_1x2(), _market_btts(), _market_ou(), _market_ou("3.5")]
        live_markets = [_market_1x2()]
        pre = _snap(1, SnapshotPhase.PREMATCH_1, pre_markets)
        live = _snap(1, SnapshotPhase.LIVE, live_markets)
        cfg = MarketsConfig(
            alert_thresholds=MarketAlertThresholdsConfig(
                retention_below_pct=50,
                any_key_market_dropped=False,  # isolate retention test
                max_odds_shift_pct=1000,
            ),
            key_markets=[],
        )

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert cmp.retention_pct == 25.0
        assert cmp.triggered_alert

    def test_big_odds_shift(self):
        pre = _snap(1, SnapshotPhase.PREMATCH_1, [_market_1x2(home_odds=2.0)])
        live = _snap(1, SnapshotPhase.LIVE, [_market_1x2(home_odds=2.7)])  # +35%
        cfg = MarketsConfig(
            alert_thresholds=MarketAlertThresholdsConfig(
                retention_below_pct=0,
                any_key_market_dropped=False,
                max_odds_shift_pct=30,
            ),
            key_markets=[],
        )

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert cmp.max_odds_shift_pct >= 30
        assert cmp.triggered_alert

    def test_row_matched_by_handicap(self):
        # Prematch has O/U 2.5 and 3.5; live only has 2.5 — should keep by market_type but diff inside
        pre = _snap(1, SnapshotPhase.PREMATCH_1, [_market_ou("2.5"), _market_ou("3.5")])
        live = _snap(1, SnapshotPhase.LIVE, [_market_ou("2.5", over=1.9, under=1.9)])
        cfg = _default_config()

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        # Both markets have same market_type_id, so markets_kept == 1
        # (current algorithm is per market_type_id, not per row)
        assert cmp.markets_kept == 1

    def test_suspended_selections_excluded_from_shift(self):
        pre = _snap(1, SnapshotPhase.PREMATCH_1, [_market_1x2(home_odds=2.0)])
        live_market = Market(
            market_type_id="3743", market_type_name="1X2 - FT", priority=1,
            rows=[MarketRow(row_id="r1", handicap=None, selections=[
                Selection(price_id="p1", name="1", type_id="3744", price=5.0, suspended=True),
                Selection(price_id="p2", name="X", type_id="3745", price=3.3, suspended=False),
                Selection(price_id="p3", name="2", type_id="3746", price=4.0, suspended=False),
            ])],
        )
        live = _snap(1, SnapshotPhase.LIVE, [live_market])
        cfg = _default_config()

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        # price=5.0 is suspended, should be excluded — shift from 3.3/4.0 is 0
        assert cmp.max_odds_shift_pct == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_market_comparator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'live_coverage_bot.core.market_comparator'`

- [ ] **Step 3: Implement market comparator**

Create `src/live_coverage_bot/core/market_comparator.py`:

```python
"""Market comparator — diffs snapshots and decides whether to alert."""

from datetime import datetime

from live_coverage_bot.config.models import MarketsConfig
from live_coverage_bot.models.markets import (
    Market,
    MarketComparison,
    MarketSnapshot,
)


def compare_snapshots(
    prematch: MarketSnapshot,
    live: MarketSnapshot,
    config: MarketsConfig,
    now: datetime,
) -> MarketComparison:
    """Produce a MarketComparison by diffing a prematch snapshot against a live snapshot."""
    pre_by_id: dict[str, Market] = {m.market_type_id: m for m in prematch.markets}
    live_by_id: dict[str, Market] = {m.market_type_id: m for m in live.markets}

    kept_ids = set(pre_by_id) & set(live_by_id)
    dropped_ids = set(pre_by_id) - set(live_by_id)
    added_ids = set(live_by_id) - set(pre_by_id)

    total_prematch = len(pre_by_id)
    retention_pct = (len(kept_ids) / total_prematch * 100) if total_prematch else 0.0

    dropped_names = [pre_by_id[mt_id].market_type_name for mt_id in dropped_ids]
    added_names = [live_by_id[mt_id].market_type_name for mt_id in added_ids]

    key_markets = set(config.key_markets)
    dropped_key_markets = [name for name in dropped_names if name in key_markets]

    odds_shifts: list[dict] = []
    max_shift = 0.0
    for mt_id in kept_ids:
        pre_m = pre_by_id[mt_id]
        live_m = live_by_id[mt_id]
        pre_rows_by_handicap = {(r.handicap or ""): r for r in pre_m.rows}
        for live_row in live_m.rows:
            pre_row = pre_rows_by_handicap.get(live_row.handicap or "")
            if pre_row is None:
                continue
            pre_sels_by_type = {s.type_id: s for s in pre_row.selections}
            for live_sel in live_row.selections:
                if live_sel.suspended:
                    continue
                pre_sel = pre_sels_by_type.get(live_sel.type_id)
                if pre_sel is None or pre_sel.suspended or pre_sel.price <= 0:
                    continue
                shift = abs(live_sel.price - pre_sel.price) / pre_sel.price * 100
                if shift > max_shift:
                    max_shift = shift
                odds_shifts.append({
                    "market": pre_m.market_type_name,
                    "selection": pre_sel.name,
                    "handicap": pre_row.handicap,
                    "prematch_price": pre_sel.price,
                    "live_price": live_sel.price,
                    "shift_pct": round(shift, 2),
                })

    thresholds = config.alert_thresholds
    triggered = (
        retention_pct < thresholds.retention_below_pct
        or (thresholds.any_key_market_dropped and bool(dropped_key_markets))
        or max_shift >= thresholds.max_odds_shift_pct
    )

    return MarketComparison(
        event_id=prematch.event_id,
        compared_at=now,
        prematch_phase=prematch.phase,
        markets_added=len(added_ids),
        markets_dropped=len(dropped_ids),
        markets_kept=len(kept_ids),
        retention_pct=round(retention_pct, 2),
        dropped_key_markets=dropped_key_markets,
        max_odds_shift_pct=round(max_shift, 2),
        triggered_alert=triggered,
        details={
            "dropped": dropped_names,
            "added": added_names,
            "odds_shifts": odds_shifts,
        },
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_market_comparator.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/core/market_comparator.py tests/test_market_comparator.py
git commit -m "feat: market comparator with retention/key-market/odds-shift alert rules"
```

---

### Task 7: Slack Client — Market Message Methods

**Files:**
- Modify: `src/live_coverage_bot/clients/slack.py`
- Modify: `tests/test_slack.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_slack.py`:

```python


class TestSlackMarketMessages:
    def test_format_market_recap(self, slack_config, sample_event):
        from live_coverage_bot.clients.slack import SlackClient
        from live_coverage_bot.models.markets import MarketComparison, SnapshotPhase

        client = SlackClient(slack_config)
        now = datetime(2026, 4, 15, 15, 12, tzinfo=UTC)
        cmp = MarketComparison(
            event_id=1,
            compared_at=now,
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0,
            markets_dropped=53,
            markets_kept=34,
            retention_pct=39.1,
            dropped_key_markets=["Both Teams To Score - FT"],
            max_odds_shift_pct=16.7,
            triggered_alert=True,
            details={
                "dropped": ["Corner markets", "Player specials"],
                "added": [],
                "odds_shifts": [
                    {"market": "1X2 - FT", "selection": "1", "handicap": None,
                     "prematch_price": 2.10, "live_price": 2.45, "shift_pct": 16.7},
                ],
            },
        )
        text = client.format_market_recap(cmp, now)
        assert "15:12" in text
        assert "Market comparison" in text
        assert "87" in text  # 53 + 34 prematch count
        assert "34 live" in text
        assert "39" in text  # retention %
        assert "Both Teams To Score - FT" in text
        assert "16.7" in text or "16" in text

    def test_format_market_anomaly_parent(self, slack_config, sample_event):
        from live_coverage_bot.clients.slack import SlackClient
        from live_coverage_bot.models.markets import MarketComparison, SnapshotPhase

        client = SlackClient(slack_config)
        cmp = MarketComparison(
            event_id=1,
            compared_at=datetime(2026, 4, 15, 15, 1, tzinfo=UTC),
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0, markets_dropped=50, markets_kept=20,
            retention_pct=28.6,
            dropped_key_markets=["1X2 - FT"],
            max_odds_shift_pct=5.0, triggered_alert=True,
            details={"dropped": [], "added": [], "odds_shifts": []},
        )
        text = client.format_market_anomaly_parent(sample_event, cmp)
        assert "MARKET ANOMALY" in text
        assert "Arsenal vs Chelsea" in text
        assert "28" in text  # retention %
        assert "1X2 - FT" in text

    async def test_post_market_recap(self, slack_config):
        from live_coverage_bot.clients.slack import SlackClient

        client = SlackClient(slack_config)
        mock_response = AsyncMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status = AsyncMock()

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            await client.post_market_recap("1234.5678", "recap text")
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["thread_ts"] == "1234.5678"

    async def test_post_market_anomaly_alert_returns_ts(self, slack_config, sample_event):
        from live_coverage_bot.clients.slack import SlackClient

        client = SlackClient(slack_config)
        mock_response = AsyncMock()
        mock_response.json.return_value = {"ok": True, "ts": "9999.0000"}
        mock_response.raise_for_status = AsyncMock()

        with patch.object(client._client, "post", return_value=mock_response):
            ts = await client.post_market_anomaly_alert(sample_event, "parent text")
            assert ts == "9999.0000"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_slack.py -v`
Expected: FAIL — `AttributeError: 'SlackClient' object has no attribute 'format_market_recap'`

- [ ] **Step 3: Implement the new Slack methods**

Add these methods to the `SlackClient` class in `src/live_coverage_bot/clients/slack.py` (before the `close` method at the end):

```python
    def format_market_recap(
        self,
        comparison: "MarketComparison",
        now: datetime,
    ) -> str:
        """Format a market comparison as a thread reply."""
        from live_coverage_bot.models.markets import MarketComparison  # noqa: F401

        time_str = now.strftime("%H:%M")
        total_prematch = comparison.markets_dropped + comparison.markets_kept

        lines: list[str] = [
            f"{time_str} \u2014 \U0001f4ca Market comparison (prematch {comparison.prematch_phase} \u2192 live)",
            f" \u2022 {total_prematch} prematch markets \u2192 {comparison.markets_kept} live "
            f"({comparison.retention_pct:.0f}% retention)",
        ]

        dropped_names = comparison.details.get("dropped", [])
        if dropped_names:
            lines.append(f" \u2022 Dropped: {comparison.markets_dropped} markets")
            for name in dropped_names[:5]:
                lines.append(f"   - {name}")
            if len(dropped_names) > 5:
                lines.append(f"   - ... and {len(dropped_names) - 5} more")

        if comparison.dropped_key_markets:
            lines.append(
                f" \u2022 \u26a0\ufe0f Key market(s) dropped: "
                f"{', '.join(comparison.dropped_key_markets)}"
            )

        odds_shifts = comparison.details.get("odds_shifts", [])
        if odds_shifts:
            biggest = max(odds_shifts, key=lambda s: s.get("shift_pct", 0))
            lines.append(
                f" \u2022 Biggest odds shift: {biggest['market']} "
                f"{biggest['selection']} "
                f"{biggest['prematch_price']} \u2192 {biggest['live_price']} "
                f"({biggest['shift_pct']:+.1f}%)"
            )

        return "\n".join(lines)

    def format_market_anomaly_parent(
        self,
        event: TrackedEvent,
        comparison: "MarketComparison",
    ) -> str:
        """Format a standalone 'market anomaly' parent message for happy-path events."""
        from live_coverage_bot.models.markets import MarketComparison  # noqa: F401

        provider_str = ", ".join(f"{p.type} #{p.id}" for p in event.provider_ids)
        competition_line = event.competition
        if event.country:
            competition_line += f" | {event.country}"
        kickoff_str = event.scheduled_kickoff.strftime("%H:%M UTC")
        total_prematch = comparison.markets_dropped + comparison.markets_kept

        lines = [
            f"\U0001f4ca MARKET ANOMALY \u2014 {event.home_team} vs {event.away_team}",
            f"\U0001f4cb {competition_line}",
            f"\u23f0 Kickoff: {kickoff_str} | Went live on time",
            f"\U0001f50c {provider_str}",
            "",
            f"{total_prematch} prematch markets \u2192 {comparison.markets_kept} live "
            f"({comparison.retention_pct:.0f}% retention)",
        ]
        if comparison.dropped_key_markets:
            lines.append(
                f"\u26a0\ufe0f Key market dropped: "
                f"{', '.join(comparison.dropped_key_markets)}"
            )
        if comparison.max_odds_shift_pct > 0:
            lines.append(f"Biggest odds shift: {comparison.max_odds_shift_pct:+.1f}%")
        return "\n".join(lines)

    async def post_market_recap(self, thread_ts: str, text: str) -> None:
        """Post a market comparison recap as a reply in an existing event thread."""
        response = await self._client.post(
            "/chat.postMessage",
            json={
                "channel": self._config.channel_id,
                "thread_ts": thread_ts,
                "text": text,
            },
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackError(f"Slack API error: {data.get('error', 'unknown')}")

    async def post_market_anomaly_alert(
        self, event: TrackedEvent, text: str
    ) -> str:
        """Post a standalone parent message for a market anomaly. Returns ts."""
        response = await self._client.post(
            "/chat.postMessage",
            json={"channel": self._config.channel_id, "text": text},
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackError(f"Slack API error: {data.get('error', 'unknown')}")
        return data["ts"]
```

Also — add the required imports at the top of `slack.py` (if not already present, check first):

```python
from live_coverage_bot.models.events import EventStatus, TrackedEvent
```

(This import already exists from v2 lifecycle work — verify before duplicating.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_slack.py -v`
Expected: All existing + 4 new tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/clients/slack.py tests/test_slack.py
git commit -m "feat: Slack market recap (thread reply) and anomaly alert (parent) formatting"
```

---

### Task 8: Market Snapshotter

**Files:**
- Create: `src/live_coverage_bot/core/market_snapshotter.py`
- Create: `tests/test_market_snapshotter.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_market_snapshotter.py`:

```python
"""Tests for the market snapshotter."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.config.models import MarketsConfig
from live_coverage_bot.core.market_snapshotter import MarketSnapshotter
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.models.markets import (
    Market,
    MarketRow,
    Selection,
    SnapshotPhase,
)


@pytest.fixture
def event_repo(db: Database) -> EventRepository:
    return EventRepository(db)


@pytest.fixture
def market_repo(db: Database) -> MarketRepository:
    return MarketRepository(db)


@pytest.fixture
def markets_config() -> MarketsConfig:
    return MarketsConfig()


@pytest.fixture
async def prematch_event_id(event_repo):
    event = TrackedEvent(
        betpawa_event_id="99001",
        home_team="A", away_team="B",
        competition="X", country="Y",
        scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
        status=EventStatus.PREMATCH,
        provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
        first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
    )
    return await event_repo.insert_event(event)


def _make_markets():
    return [Market(
        market_type_id="3743", market_type_name="1X2 - FT", priority=1,
        rows=[MarketRow(row_id="r1", handicap=None, selections=[
            Selection(price_id="p1", name="1", type_id="3744", price=2.0, suspended=False),
            Selection(price_id="p2", name="X", type_id="3745", price=3.3, suspended=False),
            Selection(price_id="p3", name="2", type_id="3746", price=4.0, suspended=False),
        ])],
    )]


class TestPhaseWindow:
    async def test_detects_prematch_60_window(self, event_repo, market_repo, markets_config, prematch_event_id):
        mock_betpawa = AsyncMock()
        mock_betpawa.get_event_markets.return_value = _make_markets()

        snapshotter = MarketSnapshotter(event_repo, market_repo, mock_betpawa, markets_config)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now = kickoff - timedelta(minutes=58)  # in the [60,55] window

        await snapshotter.run_cycle(now=now, live_transition_event_ids=set())

        phases = await market_repo.phases_taken_for_event(prematch_event_id)
        assert SnapshotPhase.PREMATCH_60 in phases

    async def test_skips_outside_window(self, event_repo, market_repo, markets_config, prematch_event_id):
        mock_betpawa = AsyncMock()
        mock_betpawa.get_event_markets.return_value = _make_markets()

        snapshotter = MarketSnapshotter(event_repo, market_repo, mock_betpawa, markets_config)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now = kickoff - timedelta(minutes=40)  # outside all windows

        await snapshotter.run_cycle(now=now, live_transition_event_ids=set())

        phases = await market_repo.phases_taken_for_event(prematch_event_id)
        assert phases == set()

    async def test_skips_already_taken_phase(self, event_repo, market_repo, markets_config, prematch_event_id):
        mock_betpawa = AsyncMock()
        mock_betpawa.get_event_markets.return_value = _make_markets()

        snapshotter = MarketSnapshotter(event_repo, market_repo, mock_betpawa, markets_config)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now = kickoff - timedelta(minutes=58)

        await snapshotter.run_cycle(now=now, live_transition_event_ids=set())
        await snapshotter.run_cycle(now=now, live_transition_event_ids=set())

        # get_event_markets should have been called only once
        assert mock_betpawa.get_event_markets.call_count == 1

    async def test_takes_live_snapshot_on_transition(self, event_repo, market_repo, markets_config, prematch_event_id):
        mock_betpawa = AsyncMock()
        mock_betpawa.get_event_markets.return_value = _make_markets()

        snapshotter = MarketSnapshotter(event_repo, market_repo, mock_betpawa, markets_config)
        now = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)

        await snapshotter.run_cycle(
            now=now,
            live_transition_event_ids={"99001"},
        )

        phases = await market_repo.phases_taken_for_event(prematch_event_id)
        assert SnapshotPhase.LIVE in phases

    async def test_disabled_does_nothing(self, event_repo, market_repo, prematch_event_id):
        mock_betpawa = AsyncMock()
        disabled_config = MarketsConfig(enabled=False)

        snapshotter = MarketSnapshotter(event_repo, market_repo, mock_betpawa, disabled_config)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        await snapshotter.run_cycle(now=kickoff - timedelta(minutes=58),
                                    live_transition_event_ids={"99001"})

        mock_betpawa.get_event_markets.assert_not_called()
        phases = await market_repo.phases_taken_for_event(prematch_event_id)
        assert phases == set()

    async def test_fetch_error_skips_phase(self, event_repo, market_repo, markets_config, prematch_event_id):
        from live_coverage_bot.clients.betpawa import BetPawaError

        mock_betpawa = AsyncMock()
        mock_betpawa.get_event_markets.side_effect = BetPawaError("API 500")

        snapshotter = MarketSnapshotter(event_repo, market_repo, mock_betpawa, markets_config)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now = kickoff - timedelta(minutes=58)

        await snapshotter.run_cycle(now=now, live_transition_event_ids=set())

        # Phase not marked as taken — can retry next cycle
        phases = await market_repo.phases_taken_for_event(prematch_event_id)
        assert SnapshotPhase.PREMATCH_60 not in phases
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_market_snapshotter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'live_coverage_bot.core.market_snapshotter'`

- [ ] **Step 3: Implement MarketSnapshotter**

Create `src/live_coverage_bot/core/market_snapshotter.py`:

```python
"""Market snapshotter — fetches and stores market snapshots on configured windows."""

import asyncio
import logging
import time
from datetime import datetime, timedelta

from live_coverage_bot.clients.betpawa import BetPawaClient, BetPawaError
from live_coverage_bot.config.models import MarketsConfig
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.models.markets import (
    Market,
    MarketSnapshot,
    SnapshotPhase,
)

logger = logging.getLogger(__name__)


class MarketSnapshotter:
    """Decides when to take market snapshots and executes them."""

    def __init__(
        self,
        event_repo: EventRepository,
        market_repo: MarketRepository,
        betpawa: BetPawaClient,
        config: MarketsConfig,
    ) -> None:
        self._events = event_repo
        self._markets = market_repo
        self._betpawa = betpawa
        self._config = config

    async def run_cycle(
        self,
        now: datetime,
        live_transition_event_ids: set[str],
    ) -> list[tuple[int, SnapshotPhase]]:
        """Run one snapshot decision cycle.

        Args:
            now: Current UTC time.
            live_transition_event_ids: BetPawa event IDs that transitioned to LIVE this cycle.

        Returns:
            List of (event_id, phase) tuples for snapshots that were taken.
        """
        if not self._config.enabled:
            return []

        # Collect all (event, phase) pairs that need a snapshot this cycle.
        plan: list[tuple[TrackedEvent, SnapshotPhase]] = []
        active = await self._events.get_active_events()
        for event in active:
            assert event.id is not None
            taken = await self._markets.phases_taken_for_event(event.id)
            for phase in self._prematch_phases_due(event, now):
                if phase not in taken:
                    plan.append((event, phase))

        # LIVE snapshots for events that just transitioned
        if live_transition_event_ids:
            # We may have a mix of active and recently-transitioned (now LIVE) events.
            for bp_id in live_transition_event_ids:
                event = await self._events.get_by_betpawa_id(bp_id)
                if event is None or event.id is None:
                    continue
                taken = await self._markets.phases_taken_for_event(event.id)
                if SnapshotPhase.LIVE not in taken:
                    plan.append((event, SnapshotPhase.LIVE))

        if not plan:
            return []

        # Run all snapshots in parallel
        results = await asyncio.gather(
            *[self._take_snapshot(event, phase, now) for event, phase in plan],
            return_exceptions=False,
        )
        return [r for r in results if r is not None]

    def _prematch_phases_due(
        self, event: TrackedEvent, now: datetime
    ) -> list[SnapshotPhase]:
        """Return prematch phases whose window currently covers `now` for this event."""
        if event.status not in (EventStatus.PREMATCH, EventStatus.LATE):
            return []

        windows = self._config.snapshot_windows
        minutes_to_kickoff = (event.scheduled_kickoff - now).total_seconds() / 60.0
        phases: list[SnapshotPhase] = []

        def in_window(window: list[int]) -> bool:
            hi, lo = window[0], window[1]  # [start_minutes_before, end_minutes_before]
            return lo <= minutes_to_kickoff <= hi

        if in_window(windows.prematch_60_min_before):
            phases.append(SnapshotPhase.PREMATCH_60)
        if in_window(windows.prematch_15_min_before):
            phases.append(SnapshotPhase.PREMATCH_15)
        if in_window(windows.prematch_1_min_before):
            phases.append(SnapshotPhase.PREMATCH_1)
        return phases

    async def _take_snapshot(
        self, event: TrackedEvent, phase: SnapshotPhase, now: datetime
    ) -> tuple[int, SnapshotPhase] | None:
        assert event.id is not None
        started = time.monotonic()
        try:
            markets = await self._betpawa.get_event_markets(event.betpawa_event_id)
        except BetPawaError as e:
            logger.warning(
                "Market snapshot fetch failed for %s %s: %s",
                event.betpawa_event_id, phase.value, e,
            )
            return None

        duration_ms = int((time.monotonic() - started) * 1000)

        total_selections = sum(len(r.selections) for m in markets for r in m.rows)
        suspended = sum(
            1 for m in markets for r in m.rows for s in r.selections if s.suspended
        )

        snapshot = MarketSnapshot(
            event_id=event.id,
            phase=phase,
            taken_at=now,
            markets=markets,
            total_market_count=len(markets),
            total_selection_count=total_selections,
            suspended_count=suspended,
        )
        try:
            await self._markets.insert_snapshot(snapshot, fetch_duration_ms=duration_ms)
        except Exception as e:
            logger.warning(
                "Snapshot insert failed for event %s phase %s: %s",
                event.betpawa_event_id, phase.value, e,
            )
            return None

        logger.info(
            "Snapshot taken: %s %s vs %s (%s) \u2014 %d markets",
            event.betpawa_event_id, event.home_team, event.away_team,
            phase.value, len(markets),
        )
        return (event.id, phase)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_market_snapshotter.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/core/market_snapshotter.py tests/test_market_snapshotter.py
git commit -m "feat: MarketSnapshotter with window detection and parallel fetches"
```

---

### Task 9: Market Reporter (Per-Event CSV + Weekly Summary)

**Files:**
- Create: `src/live_coverage_bot/core/market_reporter.py`
- Create: `tests/test_market_reporter.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_market_reporter.py`:

```python
"""Tests for the market reporter (CSV + Slack summary)."""

import csv
import io
from datetime import UTC, datetime

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.core.market_reporter import MarketReporter
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.models.markets import (
    Market,
    MarketComparison,
    MarketRow,
    MarketSnapshot,
    Selection,
    SnapshotPhase,
)


@pytest.fixture
def event_repo(db: Database) -> EventRepository:
    return EventRepository(db)


@pytest.fixture
def market_repo(db: Database) -> MarketRepository:
    return MarketRepository(db)


@pytest.fixture
def reporter(event_repo, market_repo) -> MarketReporter:
    return MarketReporter(event_repo, market_repo)


async def _seed(event_repo, market_repo):
    base = datetime(2026, 4, 14, 15, 0, tzinfo=UTC)
    event = TrackedEvent(
        betpawa_event_id="99001",
        home_team="Team A", away_team="Team B",
        competition="EPL", country="England",
        scheduled_kickoff=base, status=EventStatus.LIVE,
        provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
        first_seen_prematch=base, first_seen_live=base,
        transition_delay_sec=60,
    )
    eid = await event_repo.insert_event(event)

    market = Market(
        market_type_id="3743", market_type_name="1X2 - FT", priority=1,
        rows=[MarketRow(row_id="r1", handicap=None, selections=[
            Selection(price_id="p1", name="1", type_id="3744", price=2.0, suspended=False),
            Selection(price_id="p2", name="X", type_id="3745", price=3.3, suspended=False),
            Selection(price_id="p3", name="2", type_id="3746", price=4.0, suspended=False),
        ])],
    )
    await market_repo.insert_snapshot(MarketSnapshot(
        event_id=eid, phase=SnapshotPhase.PREMATCH_1,
        taken_at=base, markets=[market],
        total_market_count=1, total_selection_count=3, suspended_count=0,
    ))
    await market_repo.insert_snapshot(MarketSnapshot(
        event_id=eid, phase=SnapshotPhase.LIVE,
        taken_at=base, markets=[market],
        total_market_count=1, total_selection_count=3, suspended_count=0,
    ))
    await market_repo.insert_comparison(MarketComparison(
        event_id=eid,
        compared_at=base,
        prematch_phase=SnapshotPhase.PREMATCH_1,
        markets_added=0, markets_dropped=0, markets_kept=1,
        retention_pct=100.0, dropped_key_markets=[],
        max_odds_shift_pct=0.0, triggered_alert=False,
        details={"dropped": [], "added": [], "odds_shifts": []},
    ))
    return eid


class TestPerEventCsv:
    async def test_generates_per_event_csv(self, reporter, event_repo, market_repo):
        eid = await _seed(event_repo, market_repo)
        csv_text = await reporter.generate_per_event_csv(eid)

        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        # 2 snapshots × 1 market × 1 row × 3 selections = 6 rows
        assert len(rows) == 6
        first = rows[0]
        assert first["market_type_name"] == "1X2 - FT"
        assert first["phase"] in ("PREMATCH_1", "LIVE")


class TestAggregateCsv:
    async def test_generates_aggregate_csv(self, reporter, event_repo, market_repo):
        await _seed(event_repo, market_repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        csv_text = await reporter.generate_aggregate_csv(start, end)

        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["betpawa_event_id"] == "99001"
        assert "retention_pct" in rows[0]


class TestSlackSummary:
    async def test_generates_markets_summary_block(self, reporter, event_repo, market_repo):
        await _seed(event_repo, market_repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        block = await reporter.generate_markets_summary_block(start, end)
        assert "Markets" in block
        assert "1" in block  # events with snapshots count
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_market_reporter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'live_coverage_bot.core.market_reporter'`

- [ ] **Step 3: Implement market reporter**

Create `src/live_coverage_bot/core/market_reporter.py`:

```python
"""Market reporter — per-event CSV, aggregate CSV, and Slack summary block."""

import csv
import io
import logging
from collections import Counter, defaultdict
from datetime import datetime

from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository

logger = logging.getLogger(__name__)


class MarketReporter:
    """Generates market-focused reports from snapshot + comparison data."""

    def __init__(
        self, event_repo: EventRepository, market_repo: MarketRepository
    ) -> None:
        self._events = event_repo
        self._markets = market_repo

    async def generate_per_event_csv(self, event_id: int) -> str:
        """Full market timeline for one event across all phases."""
        snapshots = await self._markets.get_snapshots_for_event(event_id)
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "phase", "taken_at", "market_type_id", "market_type_name",
                "handicap", "selection_name", "selection_type_id",
                "price", "suspended",
            ],
        )
        writer.writeheader()

        for snap in snapshots:
            for market in snap.markets:
                for row in market.rows:
                    for sel in row.selections:
                        writer.writerow({
                            "phase": snap.phase.value,
                            "taken_at": snap.taken_at.isoformat(),
                            "market_type_id": market.market_type_id,
                            "market_type_name": market.market_type_name,
                            "handicap": row.handicap or "",
                            "selection_name": sel.name,
                            "selection_type_id": sel.type_id,
                            "price": sel.price,
                            "suspended": sel.suspended,
                        })
        return output.getvalue()

    async def generate_aggregate_csv(self, start: datetime, end: datetime) -> str:
        """One row per event with a comparison in the date range."""
        comparisons = await self._markets.get_comparisons_in_date_range(start, end)

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "betpawa_event_id", "home", "away", "competition", "kickoff",
                "prematch_markets", "live_markets", "retention_pct",
                "dropped_key_markets", "max_odds_shift_pct", "triggered_alert",
            ],
        )
        writer.writeheader()

        for cmp in comparisons:
            # Fetch event for context
            all_events = await self._events.get_events_in_date_range(
                datetime.min.replace(tzinfo=start.tzinfo),
                datetime.max.replace(tzinfo=start.tzinfo),
            )
            event = next((e for e in all_events if e.id == cmp.event_id), None)
            if event is None:
                continue
            writer.writerow({
                "betpawa_event_id": event.betpawa_event_id,
                "home": event.home_team,
                "away": event.away_team,
                "competition": event.competition,
                "kickoff": event.scheduled_kickoff.isoformat(),
                "prematch_markets": cmp.markets_dropped + cmp.markets_kept,
                "live_markets": cmp.markets_kept + cmp.markets_added,
                "retention_pct": cmp.retention_pct,
                "dropped_key_markets": ";".join(cmp.dropped_key_markets),
                "max_odds_shift_pct": cmp.max_odds_shift_pct,
                "triggered_alert": cmp.triggered_alert,
            })
        return output.getvalue()

    async def generate_markets_summary_block(
        self, start: datetime, end: datetime
    ) -> str:
        """Return the markets section for the weekly Slack summary."""
        comparisons = await self._markets.get_comparisons_in_date_range(start, end)
        total = len(comparisons)

        if total == 0:
            return "\u2500\u2500\u2500 Markets \u2500\u2500\u2500\n\nNo market comparisons in this period."

        avg_prematch = sum(c.markets_dropped + c.markets_kept for c in comparisons) / total
        avg_live = sum(c.markets_kept + c.markets_added for c in comparisons) / total
        avg_retention = sum(c.retention_pct for c in comparisons) / total
        ge_50_dropped = sum(1 for c in comparisons if c.retention_pct <= 50)
        ge_80_dropped = sum(1 for c in comparisons if c.retention_pct <= 20)

        dropped_key_market_counts: Counter[str] = Counter()
        for c in comparisons:
            for name in c.dropped_key_markets:
                dropped_key_market_counts[name] += 1

        # Per-market-type retention (from details.dropped + kept)
        drop_count: Counter[str] = Counter()
        keep_count: Counter[str] = Counter()
        for c in comparisons:
            for name in c.details.get("dropped", []):
                drop_count[name] += 1
            # we don't have explicit kept names; use retention as proxy — handled in summary text

        lines = [
            "\u2500\u2500\u2500 Markets \u2500\u2500\u2500",
            "",
            f"Events with market snapshots: {total}",
            f"Avg markets in prematch: {avg_prematch:.1f}",
            f"Avg markets in live: {avg_live:.1f}",
            f"Avg market retention: {avg_retention:.0f}%",
            f"Events where \u226550% of markets dropped: {ge_50_dropped}",
            f"Events where \u226580% of markets dropped: {ge_80_dropped}",
        ]

        if dropped_key_market_counts:
            lines.append("")
            lines.append("Key markets that disappeared when going live:")
            for name, count in dropped_key_market_counts.most_common(5):
                lines.append(f"  {name}: {count} events")

        if drop_count:
            lines.append("")
            lines.append("Most frequently dropped markets:")
            for name, count in drop_count.most_common(5):
                lines.append(f"  {name}: dropped in {count} events")

        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_market_reporter.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/core/market_reporter.py tests/test_market_reporter.py
git commit -m "feat: MarketReporter with per-event CSV, aggregate CSV, and Slack summary block"
```

---

### Task 10: Wire Snapshotter + Comparator into Monitoring Loop

**Files:**
- Modify: `src/live_coverage_bot/core/loop.py`
- Modify: `src/live_coverage_bot/core/__init__.py`
- Modify: `tests/test_loop.py`

- [ ] **Step 1: Update core/__init__.py exports**

Replace the FULL contents of `src/live_coverage_bot/core/__init__.py`:

```python
"""Core business logic for the live coverage bot."""

from live_coverage_bot.core.loop import MonitoringLoop
from live_coverage_bot.core.market_comparator import compare_snapshots
from live_coverage_bot.core.market_reporter import MarketReporter
from live_coverage_bot.core.market_snapshotter import MarketSnapshotter
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.core.tracker import EventLifecycleTracker

__all__ = [
    "EventLifecycleTracker",
    "MarketReporter",
    "MarketSnapshotter",
    "MonitoringLoop",
    "WeeklyReporter",
    "compare_snapshots",
]
```

- [ ] **Step 2: Add failing integration test**

Append to `tests/test_loop.py`:

```python


class TestMarketIntegration:
    async def test_loop_takes_live_snapshot_on_transition(self, db, settings):
        from live_coverage_bot.clients.models import UpcomingEvent
        from live_coverage_bot.core.tracker import EventLifecycleTracker
        from live_coverage_bot.db.market_repository import MarketRepository
        from live_coverage_bot.models.markets import (
            Market, MarketRow, Selection, SnapshotPhase,
        )
        from live_coverage_bot.clients.models import LiveEvent

        repo = EventRepository(db)
        market_repo = MarketRepository(db)

        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo
        loop._market_repo = market_repo
        loop._tracker = EventLifecycleTracker(repo,
            grace_period_minutes=5, hard_timeout_minutes=90)

        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        pids = [ProviderID(type=ProviderType.SPORTRADAR, id="12345")]

        mock_betpawa = AsyncMock()
        mock_slack = AsyncMock()
        mock_slack.post_alert.return_value = "ts_1"
        mock_slack.post_market_anomaly_alert.return_value = "ts_2"

        # Register prematch event
        mock_betpawa.get_upcoming_events.return_value = [
            UpcomingEvent(
                event_id="99001", home_team="A", away_team="B",
                competition_name="EPL", country_name="England",
                start_time=kickoff, provider_ids=pids,
            )
        ]
        mock_betpawa.get_live_events.return_value = []
        mock_betpawa.get_event_markets.return_value = [
            Market(market_type_id="3743", market_type_name="1X2 - FT", priority=1,
                rows=[MarketRow(row_id="r1", handicap=None, selections=[
                    Selection(price_id="p1", name="1", type_id="3744",
                              price=2.0, suspended=False),
                ])])
        ]

        now_reg = kickoff - timedelta(hours=2)
        await loop._poll_cycle(mock_betpawa, mock_slack,
                               now=now_reg, force_prematch=True)

        # Simulate PREMATCH_1 window and then LIVE transition
        live_event = LiveEvent(
            event_id="bp:99001", home_team="A", away_team="B",
            competition_id="1", competition_name="EPL", country_name="England",
            minute="1", home_score=0, away_score=0, start_time=kickoff,
        )
        live_event._provider_ids_override = pids
        mock_betpawa.get_live_events.return_value = [live_event]

        now_go_live = kickoff + timedelta(seconds=30)
        await loop._poll_cycle(mock_betpawa, mock_slack, now=now_go_live)

        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.LIVE

        phases = await market_repo.phases_taken_for_event(event.id)
        assert SnapshotPhase.LIVE in phases
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/test_loop.py::TestMarketIntegration -v`
Expected: FAIL — `AttributeError: 'MonitoringLoop' object has no attribute '_market_repo'` or similar.

- [ ] **Step 4: Update MonitoringLoop to integrate markets**

Modify `src/live_coverage_bot/core/loop.py`.

First, update the imports at the top (add the new imports):

```python
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from live_coverage_bot.clients.betpawa import BetPawaClient, BetPawaError
from live_coverage_bot.clients.slack import SlackClient, SlackError
from live_coverage_bot.config.models import Settings
from live_coverage_bot.core.market_comparator import compare_snapshots
from live_coverage_bot.core.market_snapshotter import MarketSnapshotter
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.core.tracker import EventLifecycleTracker
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus
```

Update `__init__` to hold `_market_repo`:

```python
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._db: Database | None = None
        self._repo: EventRepository | None = None
        self._market_repo: MarketRepository | None = None
        self._tracker: EventLifecycleTracker | None = None
        self._prematch_cycle_counter = 0
        self._last_report_date: datetime | None = None
```

Update `run` to initialize `_market_repo` and to hold a `MarketSnapshotter` inside the client `async with` block:

Find the line `self._repo = EventRepository(self._db)` and change the block to:

```python
        self._repo = EventRepository(self._db)
        self._market_repo = MarketRepository(self._db)
        self._tracker = EventLifecycleTracker(
            self._repo,
            grace_period_minutes=self._settings.thresholds.grace_period_minutes,
            hard_timeout_minutes=self._settings.thresholds.hard_timeout_minutes,
        )
```

Now modify `_poll_cycle` to invoke the snapshotter and run comparisons for LIVE transitions.

Find the end of the `_poll_cycle` method (after the last `logger.info("Cycle: ...")` call, before the method ends). Replace the entire `_poll_cycle` body with this version that adds market handling:

```python
    async def _poll_cycle(
        self,
        betpawa: BetPawaClient,
        slack: SlackClient,
        now: datetime,
        force_prematch: bool = False,
    ) -> None:
        """Execute a single poll cycle."""
        assert self._tracker is not None
        assert self._repo is not None
        assert self._market_repo is not None

        prematch_ratio = (
            self._settings.polling.prematch_interval_seconds
            // self._settings.polling.live_interval_seconds
        )
        is_first_cycle = self._prematch_cycle_counter == 0
        self._prematch_cycle_counter += 1
        should_fetch_prematch = (
            force_prematch
            or is_first_cycle
            or self._prematch_cycle_counter >= prematch_ratio
        )

        try:
            live_events = await betpawa.get_live_events()
        except BetPawaError as e:
            logger.warning("BetPawa live fetch failed, skipping cycle: %s", e)
            return

        live_betpawa_ids = BetPawaClient.build_live_betpawa_id_set(live_events)

        current_prematch_ids: set[str] | None = None
        if should_fetch_prematch:
            self._prematch_cycle_counter = 0
            try:
                upcoming = await betpawa.get_upcoming_events(
                    hours_ahead=self._settings.polling.prematch_lookahead_hours
                )
                feed = BetPawaClient.upcoming_to_tracker_feed(upcoming)
                new_ids = await self._tracker.register_prematch_events(feed, now=now)
                if new_ids:
                    logger.info("Registered %d new prematch events", len(new_ids))

                current_prematch_ids = {e.event_id for e in upcoming}

            except BetPawaError as e:
                logger.warning("BetPawa prematch fetch failed: %s", e)

        # Lifecycle transitions
        transitions = await self._tracker.check_transitions(live_betpawa_ids, now=now)
        live_transition_bp_ids: set[str] = {
            t["betpawa_event_id"]
            for t in transitions
            if t["new_status"] == EventStatus.LIVE
        }

        for t in transitions:
            await self._handle_transition(slack, t, now)

        if current_prematch_ids is not None:
            removed = await self._tracker.detect_removed(current_prematch_ids, now=now)
            if removed:
                logger.info("Detected %d removed prematch events", len(removed))

        # Market snapshots
        snapshotter = MarketSnapshotter(
            self._repo, self._market_repo, betpawa, self._settings.markets
        )
        taken = await snapshotter.run_cycle(
            now=now,
            live_transition_event_ids=live_transition_bp_ids,
        )

        # For each LIVE snapshot taken this cycle, run comparison + alert routing
        for event_id, phase in taken:
            if phase != SnapshotPhase.LIVE:
                continue
            await self._handle_market_comparison(slack, event_id, now)

        # Cycle summary
        active = await self._repo.get_active_events()
        prematch_count = sum(1 for e in active if e.status == EventStatus.PREMATCH)
        late_count = sum(1 for e in active if e.status == EventStatus.LATE)
        logger.info(
            "Cycle: %d live events, %d prematch tracked, %d late | %d transitions",
            len(live_events), prematch_count, late_count, len(transitions),
        )
```

**Note:** `SnapshotPhase` is already added to the imports at the top (see imports list above).

Now add the `_handle_market_comparison` method right after `_handle_transition`:

```python
    async def _handle_market_comparison(
        self, slack: SlackClient, event_id: int, now: datetime
    ) -> None:
        """Run comparison for a freshly stored LIVE snapshot; route to Slack if noteworthy."""
        assert self._repo is not None
        assert self._market_repo is not None

        prematch_snap = await self._market_repo.get_latest_prematch_snapshot(event_id)
        if prematch_snap is None:
            logger.info(
                "No prematch snapshot available for event %d — skipping comparison", event_id,
            )
            return

        # Get the LIVE snapshot we just stored
        all_snaps = await self._market_repo.get_snapshots_for_event(event_id)
        live_snap = next(
            (s for s in reversed(all_snaps) if s.phase == SnapshotPhase.LIVE), None
        )
        if live_snap is None:
            return

        cmp = compare_snapshots(prematch_snap, live_snap, self._settings.markets, now=now)

        try:
            await self._market_repo.insert_comparison(cmp)
        except Exception as e:
            logger.warning("Market comparison insert failed for event %d: %s", event_id, e)
            return

        if not cmp.triggered_alert:
            return

        # Routing: if event already has a parent Slack msg, thread recap there;
        # otherwise post standalone anomaly parent.
        event = await self._repo.get_by_id(event_id)
        if event is None:
            return

        try:
            if event.slack_message_ts:
                recap_text = slack.format_market_recap(cmp, now)
                await slack.post_market_recap(event.slack_message_ts, recap_text)
                logger.info(
                    "Posted market recap for %s (existing thread)",
                    event.betpawa_event_id,
                )
            else:
                parent_text = slack.format_market_anomaly_parent(event, cmp)
                ts = await slack.post_market_anomaly_alert(event, parent_text)
                assert event.id is not None
                await self._repo.update_slack_ts(event.id, ts)
                logger.info(
                    "Posted standalone market anomaly alert for %s",
                    event.betpawa_event_id,
                )
        except SlackError as e:
            logger.warning(
                "Slack market alert failed for %s: %s", event.betpawa_event_id, e
            )
```

**Add a `get_by_id` method to `EventRepository`** — this is used by `_handle_market_comparison` above. Open `src/live_coverage_bot/db/repository.py` and add this method after the existing `get_by_betpawa_id`:

```python
    async def get_by_id(self, event_id: int) -> "TrackedEvent | None":
        """Fetch event by internal DB ID."""
        row = await self._db.fetch_one(
            "SELECT * FROM events WHERE id = ?",
            (event_id,),
        )
        if row is None:
            return None
        return self._row_to_event(row)
```

- [ ] **Step 5: Run the integration test**

Run: `pytest tests/test_loop.py::TestMarketIntegration -v`
Expected: PASS.

- [ ] **Step 6: Run the full test suite**

Run: `pytest -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/live_coverage_bot/core/loop.py src/live_coverage_bot/core/__init__.py src/live_coverage_bot/db/repository.py tests/test_loop.py
git commit -m "feat: wire MarketSnapshotter + comparator into monitoring loop with Slack routing"
```

---

### Task 11: Weekly Report Integration — Markets Summary + CSVs

**Files:**
- Modify: `src/live_coverage_bot/core/loop.py`
- Modify: `src/live_coverage_bot/__main__.py`

The existing `WeeklyReporter` produces the lifecycle portion. We'll call `MarketReporter` alongside it from `_check_weekly_report` and from `--generate-report`.

- [ ] **Step 1: Extend `_check_weekly_report` in `loop.py`**

Find the `_check_weekly_report` method and replace its body with:

```python
    async def _check_weekly_report(self, slack: SlackClient, now: datetime) -> None:
        """Check if it's time to generate the weekly report."""
        assert self._repo is not None
        assert self._market_repo is not None
        cfg = self._settings.reporting
        target_weekday = WeeklyReporter.WEEKDAY_MAP.get(cfg.day.lower())
        if target_weekday is None:
            return

        if now.weekday() != target_weekday:
            return

        hour, minute = map(int, cfg.time.split(":"))
        if now.hour != hour or now.minute < minute:
            return

        if self._last_report_date and self._last_report_date.date() == now.date():
            return

        from live_coverage_bot.core.market_reporter import MarketReporter

        reporter = WeeklyReporter(self._repo, week_starts=cfg.week_starts)
        market_reporter = MarketReporter(self._repo, self._market_repo)
        start, end = reporter.compute_report_period(now)

        try:
            # Lifecycle summary (existing)
            summary = await reporter.generate_slack_summary(start, end)

            # Markets summary (new)
            if self._settings.markets.enabled:
                markets_block = await market_reporter.generate_markets_summary_block(
                    start, end
                )
                summary = summary + "\n\n" + markets_block

            await slack.post_summary(summary)

            # Lifecycle CSV (existing)
            csv_content = await reporter.generate_csv(start, end)
            output_dir = Path(cfg.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            csv_path = output_dir / f"weekly-{now.strftime('%Y-%m-%d')}.csv"
            csv_path.write_text(csv_content, encoding="utf-8")

            # Markets aggregate CSV (new)
            if self._settings.markets.enabled:
                market_csv = await market_reporter.generate_aggregate_csv(start, end)
                market_csv_path = (
                    output_dir / f"weekly-markets-{now.strftime('%Y-%m-%d')}.csv"
                )
                market_csv_path.write_text(market_csv, encoding="utf-8")

                # Per-event CSVs
                event_dir = output_dir / "events" / now.strftime("%Y-%m-%d")
                event_dir.mkdir(parents=True, exist_ok=True)
                comparisons = await self._market_repo.get_comparisons_in_date_range(
                    start, end
                )
                for cmp in comparisons:
                    per_event_csv = await market_reporter.generate_per_event_csv(
                        cmp.event_id
                    )
                    event = await self._repo.get_by_id(cmp.event_id)
                    if event is None:
                        continue
                    safe_home = "".join(c if c.isalnum() else "_" for c in event.home_team)
                    safe_away = "".join(c if c.isalnum() else "_" for c in event.away_team)
                    filename = (
                        f"{event.betpawa_event_id}_{safe_home}_vs_{safe_away}.csv"
                    )
                    (event_dir / filename).write_text(per_event_csv, encoding="utf-8")

            self._last_report_date = now
            logger.info("Weekly report generated (lifecycle + markets)")

        except Exception as e:
            logger.error("Failed to generate weekly report: %s", e)
```

- [ ] **Step 2: Extend `--generate-report` in `__main__.py`**

Replace the `_generate_report` function:

```python
async def _generate_report(settings, logger) -> int:
    """Generate a weekly report on demand."""
    db = Database(settings.database.path)
    await db.initialize()
    repo = EventRepository(db)
    market_repo = MarketRepository(db)
    reporter = WeeklyReporter(repo, week_starts=settings.reporting.week_starts)
    market_reporter = MarketReporter(repo, market_repo)

    now = datetime.now(tz=UTC)
    start, end = reporter.compute_report_period(now)

    summary = await reporter.generate_slack_summary(start, end)
    if settings.markets.enabled:
        markets_block = await market_reporter.generate_markets_summary_block(start, end)
        summary = summary + "\n\n" + markets_block
    print(summary)

    output_dir = Path(settings.reporting.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_content = await reporter.generate_csv(start, end)
    csv_path = output_dir / f"weekly-{now.strftime('%Y-%m-%d')}.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    logger.info("Lifecycle CSV written to %s", csv_path)

    if settings.markets.enabled:
        market_csv = await market_reporter.generate_aggregate_csv(start, end)
        market_csv_path = output_dir / f"weekly-markets-{now.strftime('%Y-%m-%d')}.csv"
        market_csv_path.write_text(market_csv, encoding="utf-8")
        logger.info("Markets CSV written to %s", market_csv_path)

        event_dir = output_dir / "events" / now.strftime("%Y-%m-%d")
        event_dir.mkdir(parents=True, exist_ok=True)
        comparisons = await market_repo.get_comparisons_in_date_range(start, end)
        for cmp in comparisons:
            per_event_csv = await market_reporter.generate_per_event_csv(cmp.event_id)
            event = await repo.get_by_id(cmp.event_id)
            if event is None:
                continue
            safe_home = "".join(c if c.isalnum() else "_" for c in event.home_team)
            safe_away = "".join(c if c.isalnum() else "_" for c in event.away_team)
            filename = f"{event.betpawa_event_id}_{safe_home}_vs_{safe_away}.csv"
            (event_dir / filename).write_text(per_event_csv, encoding="utf-8")
        logger.info("Per-event CSVs written to %s", event_dir)

    await db.close()
    return 0
```

Also update the imports at the top of `__main__.py`:

```python
from live_coverage_bot.config import load_config
from live_coverage_bot.core.loop import MonitoringLoop
from live_coverage_bot.core.market_reporter import MarketReporter
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
```

- [ ] **Step 3: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/live_coverage_bot/core/loop.py src/live_coverage_bot/__main__.py
git commit -m "feat: weekly report now includes markets summary, aggregate CSV, and per-event CSVs"
```
