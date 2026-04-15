# BetPawa Prematch-to-Live Monitor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the SportyBet comparison bot with a BetPawa-only prematch-to-live event lifecycle monitor that tracks transitions, alerts on problems via Slack threading, and generates weekly reports.

**Architecture:** Single async polling loop fetches BetPawa prematch + live feeds, diffs against SQLite state to detect lifecycle transitions (PREMATCH → LIVE / LATE / NEVER_LIVE / REMOVED), sends threaded Slack alerts via Web API on problems only, and generates Tuesday morning weekly reports (Slack summary + local CSV).

**Tech Stack:** Python 3.11+, httpx (async HTTP), pydantic/pydantic-settings (config + validation), aiosqlite (async SQLite), Slack Web API (chat.postMessage/chat.update), CSV stdlib module.

---

## File Structure

```
src/live_coverage_bot/
├── __init__.py                  # Update version to 2.0.0
├── __main__.py                  # Rewrite entry point
├── clients/
│   ├── __init__.py              # Update exports (remove SportyBet)
│   ├── betpawa.py               # Keep & adapt (minor changes)
│   └── slack.py                 # Rewrite for Web API
├── config/
│   ├── __init__.py              # Update exports
│   ├── models.py                # Rewrite settings schema
│   └── loader.py                # Keep (no changes)
├── core/
│   ├── __init__.py              # Update exports
│   ├── loop.py                  # Rewrite monitoring loop
│   ├── tracker.py               # Rewrite as lifecycle state machine
│   └── reporter.py              # New: weekly report generator
├── db/
│   ├── __init__.py              # New: module exports
│   ├── connection.py            # New: SQLite connection manager
│   └── repository.py            # New: event CRUD queries
└── models/
    ├── __init__.py              # New: module exports
    └── events.py                # New: EventStatus, TrackedEvent, StateChange

tests/
├── conftest.py                  # New: shared fixtures
├── test_models.py               # New: event model tests
├── test_config.py               # New: config loading tests
├── test_db.py                   # New: repository tests
├── test_slack.py                # New: Slack client tests
├── test_tracker.py              # New: lifecycle tracker tests
├── test_reporter.py             # New: report generation tests
└── test_loop.py                 # New: monitoring loop tests

Delete:
├── src/live_coverage_bot/clients/sportybet.py
├── src/live_coverage_bot/core/matcher.py
├── src/live_coverage_bot/core/prematch_cache.py
└── src/live_coverage_bot/core/formatter.py
```

---

### Task 1: Clean Up — Remove SportyBet and Obsolete Modules

**Files:**
- Delete: `src/live_coverage_bot/clients/sportybet.py`
- Delete: `src/live_coverage_bot/core/matcher.py`
- Delete: `src/live_coverage_bot/core/prematch_cache.py`
- Delete: `src/live_coverage_bot/core/formatter.py`
- Modify: `src/live_coverage_bot/clients/__init__.py`
- Modify: `src/live_coverage_bot/core/__init__.py`
- Modify: `src/live_coverage_bot/__init__.py`

- [ ] **Step 1: Delete sportybet.py**

```bash
rm src/live_coverage_bot/clients/sportybet.py
```

- [ ] **Step 2: Delete matcher.py, prematch_cache.py, formatter.py**

```bash
rm src/live_coverage_bot/core/matcher.py
rm src/live_coverage_bot/core/prematch_cache.py
rm src/live_coverage_bot/core/formatter.py
```

- [ ] **Step 3: Update clients/__init__.py**

Replace the full contents of `src/live_coverage_bot/clients/__init__.py`:

```python
"""API clients for BetPawa and Slack."""

from live_coverage_bot.clients.betpawa import BetPawaClient, BetPawaError
from live_coverage_bot.clients.models import LiveEvent, ProviderID, ProviderType, UpcomingEvent
from live_coverage_bot.clients.slack import SlackClient, SlackError

__all__ = [
    "BetPawaClient",
    "BetPawaError",
    "LiveEvent",
    "ProviderID",
    "ProviderType",
    "SlackClient",
    "SlackError",
    "UpcomingEvent",
]
```

- [ ] **Step 4: Update core/__init__.py**

Replace the full contents of `src/live_coverage_bot/core/__init__.py`:

```python
"""Core business logic for the live coverage bot."""

from live_coverage_bot.core.loop import MonitoringLoop

__all__ = [
    "MonitoringLoop",
]
```

Note: We'll add tracker and reporter exports back as we build them.

- [ ] **Step 5: Update version to 2.0.0**

In `src/live_coverage_bot/__init__.py`:

```python
"""Live Coverage Bot - BetPawa prematch-to-live event monitor."""

__version__ = "2.0.0"
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: remove sportybet and obsolete modules for v2 redesign"
```

---

### Task 2: Add aiosqlite Dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add aiosqlite to dependencies**

In `pyproject.toml`, update the dependencies list:

```toml
dependencies = [
    "httpx>=0.27",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "pyyaml>=6.0",
    "aiosqlite>=0.20",
]
```

- [ ] **Step 2: Install updated dependencies**

```bash
pip install -e ".[dev]"
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add aiosqlite dependency for event persistence"
```

---

### Task 3: New Event Models

**Files:**
- Create: `src/live_coverage_bot/models/__init__.py`
- Create: `src/live_coverage_bot/models/events.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Create models package __init__.py**

Create `src/live_coverage_bot/models/__init__.py`:

```python
"""Domain models for event lifecycle tracking."""

from live_coverage_bot.models.events import EventStatus, StateChange, TrackedEvent

__all__ = [
    "EventStatus",
    "StateChange",
    "TrackedEvent",
]
```

- [ ] **Step 2: Write failing tests for EventStatus and TrackedEvent**

Create `tests/test_models.py`:

```python
"""Tests for event lifecycle models."""

from datetime import UTC, datetime

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.models.events import EventStatus, StateChange, TrackedEvent


class TestEventStatus:
    def test_all_statuses_exist(self):
        assert EventStatus.PREMATCH == "PREMATCH"
        assert EventStatus.LIVE == "LIVE"
        assert EventStatus.LATE == "LATE"
        assert EventStatus.NEVER_LIVE == "NEVER_LIVE"
        assert EventStatus.REMOVED == "REMOVED"

    def test_terminal_states(self):
        assert EventStatus.LIVE.is_terminal
        assert EventStatus.NEVER_LIVE.is_terminal
        assert EventStatus.REMOVED.is_terminal
        assert not EventStatus.PREMATCH.is_terminal
        assert not EventStatus.LATE.is_terminal


class TestTrackedEvent:
    def test_create_from_upcoming(self):
        provider_ids = [ProviderID(type=ProviderType.SPORTRADAR, id="12345")]
        event = TrackedEvent(
            betpawa_event_id="99001",
            home_team="Arsenal",
            away_team="Chelsea",
            competition="England Premier League",
            country="England",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=provider_ids,
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        assert event.betpawa_event_id == "99001"
        assert event.status == EventStatus.PREMATCH
        assert event.first_seen_live is None
        assert event.transition_delay_sec is None
        assert event.slack_message_ts is None

    def test_provider_ids_json_serialization(self):
        provider_ids = [
            ProviderID(type=ProviderType.SPORTRADAR, id="12345"),
            ProviderID(type=ProviderType.GENIUSSPORTS, id="67890"),
        ]
        event = TrackedEvent(
            betpawa_event_id="99001",
            home_team="Arsenal",
            away_team="Chelsea",
            competition="England Premier League",
            country="England",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=provider_ids,
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        json_str = event.provider_ids_json
        assert '"SPORTRADAR"' in json_str
        assert '"12345"' in json_str
        assert '"GENIUSSPORTS"' in json_str
        assert '"67890"' in json_str


class TestStateChange:
    def test_create_state_change(self):
        change = StateChange(
            event_id=1,
            old_status=EventStatus.PREMATCH,
            new_status=EventStatus.LATE,
            changed_at=datetime(2026, 4, 15, 15, 5, tzinfo=UTC),
            details="5min past kickoff",
        )
        assert change.event_id == 1
        assert change.old_status == EventStatus.PREMATCH
        assert change.new_status == EventStatus.LATE
        assert change.details == "5min past kickoff"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest tests/test_models.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'live_coverage_bot.models.events'`

- [ ] **Step 4: Implement event models**

Create `src/live_coverage_bot/models/events.py`:

```python
"""Domain models for event lifecycle tracking."""

import json
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from live_coverage_bot.clients.models import ProviderID


class EventStatus(StrEnum):
    """Event lifecycle states."""

    PREMATCH = "PREMATCH"
    LIVE = "LIVE"
    LATE = "LATE"
    NEVER_LIVE = "NEVER_LIVE"
    REMOVED = "REMOVED"

    @property
    def is_terminal(self) -> bool:
        """Whether this status represents a final state (no further transitions)."""
        return self in (EventStatus.LIVE, EventStatus.NEVER_LIVE, EventStatus.REMOVED)


class TrackedEvent(BaseModel):
    """An event being tracked through its prematch-to-live lifecycle."""

    id: int | None = None  # DB primary key, None before insert
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
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def provider_ids_json(self) -> str:
        """Serialize provider_ids to JSON string for DB storage."""
        return json.dumps(
            [{"type": pid.type.value, "id": pid.id} for pid in self.provider_ids]
        )

    @staticmethod
    def provider_ids_from_json(json_str: str) -> list[ProviderID]:
        """Deserialize provider_ids from JSON string."""
        data = json.loads(json_str)
        return [ProviderID(type=item["type"], id=item["id"]) for item in data]


class StateChange(BaseModel):
    """Record of an event state transition."""

    id: int | None = None  # DB primary key, None before insert
    event_id: int  # FK to TrackedEvent.id
    old_status: EventStatus
    new_status: EventStatus
    changed_at: datetime
    details: str | None = None
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_models.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/models/ tests/test_models.py
git commit -m "feat: add event lifecycle models (EventStatus, TrackedEvent, StateChange)"
```

---

### Task 4: New Configuration Schema

**Files:**
- Modify: `src/live_coverage_bot/config/models.py`
- Modify: `src/live_coverage_bot/config/__init__.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for new config**

Create `tests/test_config.py`:

```python
"""Tests for configuration models."""

from live_coverage_bot.config.models import (
    BetPawaConfig,
    DatabaseConfig,
    PollingConfig,
    ReportingConfig,
    Settings,
    SlackConfig,
    ThresholdConfig,
)


class TestPollingConfig:
    def test_defaults(self):
        config = PollingConfig()
        assert config.live_interval_seconds == 30
        assert config.prematch_interval_seconds == 150
        assert config.prematch_lookahead_hours == 3


class TestThresholdConfig:
    def test_defaults(self):
        config = ThresholdConfig()
        assert config.grace_period_minutes == 5
        assert config.hard_timeout_minutes == 90


class TestDatabaseConfig:
    def test_defaults(self):
        config = DatabaseConfig()
        assert config.path == "data/events.db"
        assert config.retention_days == 30


class TestReportingConfig:
    def test_defaults(self):
        config = ReportingConfig()
        assert config.day == "tuesday"
        assert config.time == "08:00"
        assert config.week_starts == "tuesday"
        assert config.output_dir == "reports/"


class TestSlackConfig:
    def test_requires_bot_token(self):
        config = SlackConfig(bot_token="xoxb-test", channel_id="C123")
        assert config.bot_token == "xoxb-test"
        assert config.channel_id == "C123"
        assert config.summary_channel_id is None

    def test_summary_channel_defaults_to_none(self):
        config = SlackConfig(bot_token="xoxb-test", channel_id="C123")
        assert config.summary_channel_id is None


class TestSettings:
    def test_loads_with_minimal_config(self):
        settings = Settings(
            slack=SlackConfig(bot_token="xoxb-test", channel_id="C123"),
            _env_file=None,
        )
        assert settings.polling.live_interval_seconds == 30
        assert settings.thresholds.grace_period_minutes == 5
        assert settings.database.path == "data/events.db"
        assert settings.betpawa.brand == "betpawa-nigeria"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_config.py -v
```

Expected: FAIL — `ImportError: cannot import name 'PollingConfig'`

- [ ] **Step 3: Implement new config models**

Replace the full contents of `src/live_coverage_bot/config/models.py`:

```python
"""Configuration schema models for Live Coverage Bot v2."""

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class PollingConfig(BaseModel):
    """Polling interval configuration."""

    live_interval_seconds: int = 30
    prematch_interval_seconds: int = 150
    prematch_lookahead_hours: int = 3


class ThresholdConfig(BaseModel):
    """Event lifecycle threshold configuration."""

    grace_period_minutes: int = 5
    hard_timeout_minutes: int = 90


class BetPawaConfig(BaseModel):
    """BetPawa API configuration with required headers."""

    base_url: str = "https://www.betpawa.ng/api/sportsbook/v3"
    brand: str = "betpawa-nigeria"
    language: str = "en"
    device_type: str = "web"


class SlackConfig(BaseModel):
    """Slack Web API configuration."""

    bot_token: str
    channel_id: str
    summary_channel_id: str | None = None


class DatabaseConfig(BaseModel):
    """SQLite database configuration."""

    path: str = "data/events.db"
    retention_days: int = 30


class ReportingConfig(BaseModel):
    """Weekly report configuration."""

    day: str = "tuesday"
    time: str = "08:00"
    week_starts: str = "tuesday"
    output_dir: str = "reports/"


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

    model_config = SettingsConfigDict(
        env_prefix="LCB_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )
```

- [ ] **Step 4: Update config/__init__.py**

Replace the full contents of `src/live_coverage_bot/config/__init__.py`:

```python
"""Configuration module for Live Coverage Bot."""

from .loader import load_config
from .models import (
    BetPawaConfig,
    DatabaseConfig,
    PollingConfig,
    ReportingConfig,
    Settings,
    SlackConfig,
    ThresholdConfig,
)

__all__ = [
    "BetPawaConfig",
    "DatabaseConfig",
    "PollingConfig",
    "ReportingConfig",
    "Settings",
    "SlackConfig",
    "ThresholdConfig",
    "load_config",
]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_config.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/config/ tests/test_config.py
git commit -m "feat: new config schema for v2 (polling, thresholds, database, reporting)"
```

---

### Task 5: SQLite Database Layer

**Files:**
- Create: `src/live_coverage_bot/db/__init__.py`
- Create: `src/live_coverage_bot/db/connection.py`
- Create: `src/live_coverage_bot/db/repository.py`
- Create: `tests/conftest.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Create db package __init__.py**

Create `src/live_coverage_bot/db/__init__.py`:

```python
"""Database layer for event persistence."""

from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository

__all__ = [
    "Database",
    "EventRepository",
]
```

- [ ] **Step 2: Create shared test fixtures**

Create `tests/conftest.py`:

```python
"""Shared test fixtures."""

import pytest

from live_coverage_bot.db.connection import Database


@pytest.fixture
async def db():
    """Create an in-memory database for testing."""
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()
```

- [ ] **Step 3: Write failing tests for Database connection**

Create `tests/test_db.py`:

```python
"""Tests for SQLite database layer."""

from datetime import UTC, datetime

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent


class TestDatabase:
    async def test_initialize_creates_tables(self, db: Database):
        tables = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = [row["name"] for row in tables]
        assert "events" in table_names
        assert "event_state_changes" in table_names

    async def test_initialize_creates_indexes(self, db: Database):
        indexes = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
        index_names = [row["name"] for row in indexes]
        assert "idx_events_status" in index_names
        assert "idx_events_kickoff" in index_names


class TestEventRepository:
    @pytest.fixture
    def repo(self, db: Database) -> EventRepository:
        return EventRepository(db)

    def _make_event(self, **overrides) -> TrackedEvent:
        defaults = dict(
            betpawa_event_id="99001",
            home_team="Arsenal",
            away_team="Chelsea",
            competition="England Premier League",
            country="England",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        defaults.update(overrides)
        return TrackedEvent(**defaults)

    async def test_insert_and_get_by_betpawa_id(self, repo: EventRepository):
        event = self._make_event()
        row_id = await repo.insert_event(event)
        assert row_id > 0

        fetched = await repo.get_by_betpawa_id("99001")
        assert fetched is not None
        assert fetched.id == row_id
        assert fetched.home_team == "Arsenal"
        assert fetched.status == EventStatus.PREMATCH
        assert len(fetched.provider_ids) == 1
        assert fetched.provider_ids[0].type == ProviderType.SPORTRADAR

    async def test_get_by_betpawa_id_not_found(self, repo: EventRepository):
        result = await repo.get_by_betpawa_id("nonexistent")
        assert result is None

    async def test_update_status(self, repo: EventRepository):
        event = self._make_event()
        row_id = await repo.insert_event(event)

        now = datetime(2026, 4, 15, 15, 5, tzinfo=UTC)
        await repo.update_status(row_id, EventStatus.LATE, updated_at=now)

        fetched = await repo.get_by_betpawa_id("99001")
        assert fetched is not None
        assert fetched.status == EventStatus.LATE

    async def test_update_live_fields(self, repo: EventRepository):
        event = self._make_event()
        row_id = await repo.insert_event(event)

        live_time = datetime(2026, 4, 15, 15, 7, tzinfo=UTC)
        await repo.update_live_fields(
            row_id, first_seen_live=live_time, transition_delay_sec=420
        )

        fetched = await repo.get_by_betpawa_id("99001")
        assert fetched is not None
        assert fetched.first_seen_live == live_time
        assert fetched.transition_delay_sec == 420

    async def test_update_slack_ts(self, repo: EventRepository):
        event = self._make_event()
        row_id = await repo.insert_event(event)

        await repo.update_slack_ts(row_id, "1234567890.123456")

        fetched = await repo.get_by_betpawa_id("99001")
        assert fetched is not None
        assert fetched.slack_message_ts == "1234567890.123456"

    async def test_get_active_events(self, repo: EventRepository):
        # Insert PREMATCH event (active)
        await repo.insert_event(self._make_event(betpawa_event_id="1"))
        # Insert LATE event (active)
        e2 = self._make_event(betpawa_event_id="2", status=EventStatus.LATE)
        await repo.insert_event(e2)
        # Insert LIVE event (terminal, not active)
        e3 = self._make_event(betpawa_event_id="3", status=EventStatus.LIVE)
        await repo.insert_event(e3)

        active = await repo.get_active_events()
        active_ids = {e.betpawa_event_id for e in active}
        assert active_ids == {"1", "2"}

    async def test_insert_state_change(self, repo: EventRepository):
        event = self._make_event()
        row_id = await repo.insert_event(event)

        now = datetime(2026, 4, 15, 15, 5, tzinfo=UTC)
        await repo.insert_state_change(
            event_id=row_id,
            old_status=EventStatus.PREMATCH,
            new_status=EventStatus.LATE,
            changed_at=now,
            details="5min past kickoff",
        )

        changes = await repo.get_state_changes(row_id)
        assert len(changes) == 1
        assert changes[0].old_status == EventStatus.PREMATCH
        assert changes[0].new_status == EventStatus.LATE
        assert changes[0].details == "5min past kickoff"

    async def test_get_events_in_date_range(self, repo: EventRepository):
        await repo.insert_event(self._make_event(
            betpawa_event_id="1",
            scheduled_kickoff=datetime(2026, 4, 14, 15, 0, tzinfo=UTC),
        ))
        await repo.insert_event(self._make_event(
            betpawa_event_id="2",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
        ))
        await repo.insert_event(self._make_event(
            betpawa_event_id="3",
            scheduled_kickoff=datetime(2026, 4, 16, 15, 0, tzinfo=UTC),
        ))

        start = datetime(2026, 4, 14, 0, 0, tzinfo=UTC)
        end = datetime(2026, 4, 15, 23, 59, tzinfo=UTC)
        events = await repo.get_events_in_date_range(start, end)
        ids = {e.betpawa_event_id for e in events}
        assert ids == {"1", "2"}

    async def test_delete_old_events(self, repo: EventRepository):
        await repo.insert_event(self._make_event(
            betpawa_event_id="old",
            scheduled_kickoff=datetime(2026, 3, 1, 15, 0, tzinfo=UTC),
            status=EventStatus.LIVE,
        ))
        await repo.insert_event(self._make_event(
            betpawa_event_id="recent",
            scheduled_kickoff=datetime(2026, 4, 14, 15, 0, tzinfo=UTC),
        ))

        cutoff = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
        deleted = await repo.delete_events_before(cutoff)
        assert deleted == 1

        remaining = await repo.get_by_betpawa_id("recent")
        assert remaining is not None
        gone = await repo.get_by_betpawa_id("old")
        assert gone is None
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
python -m pytest tests/test_db.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'live_coverage_bot.db.connection'`

- [ ] **Step 5: Implement Database connection manager**

Create `src/live_coverage_bot/db/connection.py`:

```python
"""SQLite database connection manager."""

import logging
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

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

CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE INDEX IF NOT EXISTS idx_events_kickoff ON events(scheduled_kickoff);
"""


class Database:
    """Async SQLite database manager."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open connection and create tables."""
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.commit()
        logger.info("Database initialized at %s", self._path)

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Cursor:
        """Execute a single SQL statement."""
        assert self._conn is not None
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cursor

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        """Fetch a single row as a dict."""
        assert self._conn is not None
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Fetch all rows as a list of dicts."""
        assert self._conn is not None
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
```

- [ ] **Step 6: Implement EventRepository**

Create `src/live_coverage_bot/db/repository.py`:

```python
"""Event CRUD operations against SQLite."""

from datetime import datetime

from live_coverage_bot.db.connection import Database
from live_coverage_bot.models.events import EventStatus, StateChange, TrackedEvent


class EventRepository:
    """Repository for tracked event persistence."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_event(self, event: TrackedEvent) -> int:
        """Insert a new tracked event. Returns the row ID."""
        cursor = await self._db.execute(
            """INSERT INTO events
            (betpawa_event_id, home_team, away_team, competition, country,
             scheduled_kickoff, status, provider_ids, first_seen_prematch,
             first_seen_live, transition_delay_sec, slack_message_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.betpawa_event_id,
                event.home_team,
                event.away_team,
                event.competition,
                event.country,
                event.scheduled_kickoff.isoformat(),
                event.status.value,
                event.provider_ids_json,
                event.first_seen_prematch.isoformat(),
                event.first_seen_live.isoformat() if event.first_seen_live else None,
                event.transition_delay_sec,
                event.slack_message_ts,
            ),
        )
        return cursor.lastrowid

    async def get_by_betpawa_id(self, betpawa_event_id: str) -> TrackedEvent | None:
        """Fetch event by BetPawa event ID."""
        row = await self._db.fetch_one(
            "SELECT * FROM events WHERE betpawa_event_id = ?",
            (betpawa_event_id,),
        )
        if row is None:
            return None
        return self._row_to_event(row)

    async def get_active_events(self) -> list[TrackedEvent]:
        """Fetch all events in non-terminal states (PREMATCH, LATE)."""
        rows = await self._db.fetch_all(
            "SELECT * FROM events WHERE status IN (?, ?)",
            (EventStatus.PREMATCH.value, EventStatus.LATE.value),
        )
        return [self._row_to_event(row) for row in rows]

    async def update_status(
        self, event_id: int, status: EventStatus, updated_at: datetime
    ) -> None:
        """Update event status."""
        await self._db.execute(
            "UPDATE events SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, updated_at.isoformat(), event_id),
        )

    async def update_live_fields(
        self,
        event_id: int,
        first_seen_live: datetime,
        transition_delay_sec: int,
    ) -> None:
        """Update live-specific fields when event goes live."""
        await self._db.execute(
            """UPDATE events SET first_seen_live = ?, transition_delay_sec = ?,
            updated_at = ? WHERE id = ?""",
            (
                first_seen_live.isoformat(),
                transition_delay_sec,
                first_seen_live.isoformat(),
                event_id,
            ),
        )

    async def update_slack_ts(self, event_id: int, slack_ts: str) -> None:
        """Store Slack message timestamp for an event."""
        await self._db.execute(
            "UPDATE events SET slack_message_ts = ? WHERE id = ?",
            (slack_ts, event_id),
        )

    async def insert_state_change(
        self,
        event_id: int,
        old_status: EventStatus,
        new_status: EventStatus,
        changed_at: datetime,
        details: str | None = None,
    ) -> None:
        """Record an event state transition."""
        await self._db.execute(
            """INSERT INTO event_state_changes
            (event_id, old_status, new_status, changed_at, details)
            VALUES (?, ?, ?, ?, ?)""",
            (
                event_id,
                old_status.value,
                new_status.value,
                changed_at.isoformat(),
                details,
            ),
        )

    async def get_state_changes(self, event_id: int) -> list[StateChange]:
        """Fetch all state changes for an event, ordered chronologically."""
        rows = await self._db.fetch_all(
            "SELECT * FROM event_state_changes WHERE event_id = ? ORDER BY changed_at",
            (event_id,),
        )
        return [
            StateChange(
                id=row["id"],
                event_id=row["event_id"],
                old_status=EventStatus(row["old_status"]),
                new_status=EventStatus(row["new_status"]),
                changed_at=datetime.fromisoformat(row["changed_at"]),
                details=row["details"],
            )
            for row in rows
        ]

    async def get_events_in_date_range(
        self, start: datetime, end: datetime
    ) -> list[TrackedEvent]:
        """Fetch events with kickoff in the given range."""
        rows = await self._db.fetch_all(
            "SELECT * FROM events WHERE scheduled_kickoff >= ? AND scheduled_kickoff <= ?",
            (start.isoformat(), end.isoformat()),
        )
        return [self._row_to_event(row) for row in rows]

    async def delete_events_before(self, cutoff: datetime) -> int:
        """Delete events with kickoff before the cutoff. Returns count deleted."""
        cursor = await self._db.execute(
            "DELETE FROM events WHERE scheduled_kickoff < ?",
            (cutoff.isoformat(),),
        )
        return cursor.rowcount

    def _row_to_event(self, row: dict) -> TrackedEvent:
        """Convert a database row to a TrackedEvent."""
        return TrackedEvent(
            id=row["id"],
            betpawa_event_id=row["betpawa_event_id"],
            home_team=row["home_team"],
            away_team=row["away_team"],
            competition=row["competition"],
            country=row["country"],
            scheduled_kickoff=datetime.fromisoformat(row["scheduled_kickoff"]),
            status=EventStatus(row["status"]),
            provider_ids=TrackedEvent.provider_ids_from_json(row["provider_ids"]),
            first_seen_prematch=datetime.fromisoformat(row["first_seen_prematch"]),
            first_seen_live=(
                datetime.fromisoformat(row["first_seen_live"])
                if row["first_seen_live"]
                else None
            ),
            transition_delay_sec=row["transition_delay_sec"],
            slack_message_ts=row["slack_message_ts"],
            created_at=(
                datetime.fromisoformat(row["created_at"])
                if row["created_at"]
                else None
            ),
            updated_at=(
                datetime.fromisoformat(row["updated_at"])
                if row["updated_at"]
                else None
            ),
        )
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
python -m pytest tests/test_db.py -v
```

Expected: All 11 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/live_coverage_bot/db/ tests/conftest.py tests/test_db.py
git commit -m "feat: SQLite database layer with event repository and state change tracking"
```

---

### Task 6: Slack Web API Client

**Files:**
- Modify: `src/live_coverage_bot/clients/slack.py`
- Create: `tests/test_slack.py`

- [ ] **Step 1: Write failing tests for Slack client**

Create `tests/test_slack.py`:

```python
"""Tests for Slack Web API client."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from live_coverage_bot.clients.slack import SlackClient, SlackError
from live_coverage_bot.config.models import SlackConfig
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.clients.models import ProviderID, ProviderType


@pytest.fixture
def slack_config():
    return SlackConfig(bot_token="xoxb-test-token", channel_id="C12345")


@pytest.fixture
def sample_event():
    return TrackedEvent(
        id=1,
        betpawa_event_id="99001",
        home_team="Arsenal",
        away_team="Chelsea",
        competition="England Premier League",
        country="England",
        scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
        status=EventStatus.LATE,
        provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
        first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
    )


class TestSlackMessageFormatting:
    def test_format_late_message(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        now = datetime(2026, 4, 15, 15, 12, tzinfo=UTC)
        text = client.format_parent_message(sample_event, now)
        assert "LATE" in text
        assert "Arsenal vs Chelsea" in text
        assert "England Premier League" in text
        assert "SPORTRADAR" in text
        assert "12345" in text

    def test_format_went_live_message(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.LIVE
        sample_event.first_seen_live = datetime(2026, 4, 15, 15, 12, tzinfo=UTC)
        sample_event.transition_delay_sec = 720
        text = client.format_parent_message(sample_event)
        assert "WENT LIVE" in text
        assert "+12min" in text

    def test_format_never_live_message(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.NEVER_LIVE
        text = client.format_parent_message(sample_event)
        assert "NEVER LIVE" in text

    def test_format_thread_reply(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        now = datetime(2026, 4, 15, 15, 5, tzinfo=UTC)
        text = client.format_thread_reply(
            EventStatus.PREMATCH, EventStatus.LATE, now, "5min past kickoff"
        )
        assert "15:05" in text
        assert "5min past kickoff" in text


class TestSlackApiCalls:
    async def test_post_alert_returns_ts(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        mock_response = AsyncMock()
        mock_response.json.return_value = {"ok": True, "ts": "1234567890.123456"}
        mock_response.raise_for_status = AsyncMock()

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            ts = await client.post_alert(sample_event, datetime.now(tz=UTC))
            assert ts == "1234567890.123456"
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["channel"] == "C12345"

    async def test_post_alert_raises_on_not_ok(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        mock_response = AsyncMock()
        mock_response.json.return_value = {"ok": False, "error": "channel_not_found"}
        mock_response.raise_for_status = AsyncMock()

        with patch.object(client._client, "post", return_value=mock_response):
            with pytest.raises(SlackError, match="channel_not_found"):
                await client.post_alert(sample_event, datetime.now(tz=UTC))

    async def test_update_message(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        mock_response = AsyncMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status = AsyncMock()

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            await client.update_message("1234.5678", sample_event)
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["ts"] == "1234.5678"

    async def test_post_thread_reply(self, slack_config):
        client = SlackClient(slack_config)
        mock_response = AsyncMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status = AsyncMock()

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            await client.post_thread_reply("1234.5678", "test reply text")
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["thread_ts"] == "1234.5678"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_slack.py -v
```

Expected: FAIL — `ImportError: cannot import name 'SlackClient'`

- [ ] **Step 3: Implement Slack Web API client**

Replace the full contents of `src/live_coverage_bot/clients/slack.py`:

```python
"""Slack Web API client for threaded event alerts."""

import logging
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

import httpx

from live_coverage_bot.config.models import SlackConfig
from live_coverage_bot.models.events import EventStatus, TrackedEvent

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"


class SlackError(Exception):
    """Error raised when Slack API operations fail."""


class SlackClient:
    """Async Slack Web API client for threaded event lifecycle alerts."""

    def __init__(self, config: SlackConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=SLACK_API_BASE,
            timeout=10.0,
            headers={"Authorization": f"Bearer {config.bot_token}"},
        )

    def format_parent_message(
        self, event: TrackedEvent, now: datetime | None = None
    ) -> str:
        """Format the parent Slack message based on current event status."""
        provider_str = ", ".join(
            f"{p.type} #{p.id}" for p in event.provider_ids
        )
        competition_line = event.competition
        if event.country:
            competition_line += f" | {event.country}"

        kickoff_str = event.scheduled_kickoff.strftime("%H:%M UTC")

        if event.status == EventStatus.LATE:
            if now is None:
                now = datetime.now(tz=UTC)
            delay_min = int((now - event.scheduled_kickoff).total_seconds() / 60)
            return (
                f"\U0001f7e1 LATE \u2014 {event.home_team} vs {event.away_team}\n"
                f"\U0001f4cb {competition_line}\n"
                f"\u23f0 Kickoff: {kickoff_str} | Now: {delay_min}min late\n"
                f"\U0001f50c {provider_str}"
            )

        if event.status == EventStatus.LIVE:
            live_str = ""
            delay_str = ""
            if event.first_seen_live:
                live_str = event.first_seen_live.strftime("%H:%M UTC")
            if event.transition_delay_sec is not None:
                delay_min = event.transition_delay_sec // 60
                delay_str = f"+{delay_min}min"
            return (
                f"\U0001f7e2 WENT LIVE \u2014 {event.home_team} vs {event.away_team}\n"
                f"\U0001f4cb {competition_line}\n"
                f"\u23f0 Kickoff: {kickoff_str} | Live at: {live_str} ({delay_str})\n"
                f"\U0001f50c {provider_str}"
            )

        if event.status == EventStatus.NEVER_LIVE:
            return (
                f"\U0001f534 NEVER LIVE \u2014 {event.home_team} vs {event.away_team}\n"
                f"\U0001f4cb {competition_line}\n"
                f"\u23f0 Kickoff: {kickoff_str} | Timed out after "
                f"{int((datetime.now(tz=UTC) - event.scheduled_kickoff).total_seconds() / 60)}min\n"
                f"\U0001f50c {provider_str}"
            )

        return f"{event.home_team} vs {event.away_team} [{event.status}]"

    def format_thread_reply(
        self,
        old_status: EventStatus,
        new_status: EventStatus,
        changed_at: datetime,
        details: str | None = None,
    ) -> str:
        """Format a thread reply for a state change."""
        time_str = changed_at.strftime("%H:%M")
        detail_str = f" \u2014 {details}" if details else ""

        if new_status == EventStatus.LATE:
            return f"{time_str} \u2014 \u26a0\ufe0f Not live yet{detail_str}"
        if new_status == EventStatus.LIVE:
            return f"{time_str} \u2014 \u2705 Went live{detail_str}"
        if new_status == EventStatus.NEVER_LIVE:
            return f"{time_str} \u2014 \U0001f6d1 Never went live{detail_str}"

        return f"{time_str} \u2014 {old_status} \u2192 {new_status}{detail_str}"

    async def post_alert(self, event: TrackedEvent, now: datetime) -> str:
        """Post a new alert message. Returns the message ts."""
        text = self.format_parent_message(event, now)
        response = await self._client.post(
            "/chat.postMessage",
            json={"channel": self._config.channel_id, "text": text},
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackError(f"Slack API error: {data.get('error', 'unknown')}")
        return data["ts"]

    async def update_message(
        self, ts: str, event: TrackedEvent, now: datetime | None = None
    ) -> None:
        """Update an existing parent message."""
        text = self.format_parent_message(event, now)
        response = await self._client.post(
            "/chat.update",
            json={"channel": self._config.channel_id, "ts": ts, "text": text},
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackError(f"Slack API error: {data.get('error', 'unknown')}")

    async def post_thread_reply(self, thread_ts: str, text: str) -> None:
        """Post a reply in a message thread."""
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

    async def post_summary(self, text: str) -> None:
        """Post a weekly summary message to the summary channel."""
        channel = self._config.summary_channel_id or self._config.channel_id
        response = await self._client.post(
            "/chat.postMessage",
            json={"channel": channel, "text": text},
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackError(f"Slack API error: {data.get('error', 'unknown')}")

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_slack.py -v
```

Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/clients/slack.py tests/test_slack.py
git commit -m "feat: Slack Web API client with threading support (post, update, reply)"
```

---

### Task 7: Event Lifecycle Tracker

**Files:**
- Modify: `src/live_coverage_bot/core/tracker.py`
- Create: `tests/test_tracker.py`

- [ ] **Step 1: Write failing tests for lifecycle tracker**

Create `tests/test_tracker.py`:

```python
"""Tests for event lifecycle tracker."""

from datetime import UTC, datetime, timedelta

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.core.tracker import EventLifecycleTracker
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent


@pytest.fixture
def repo(db: Database) -> EventRepository:
    return EventRepository(db)


@pytest.fixture
def tracker(repo: EventRepository) -> EventLifecycleTracker:
    return EventLifecycleTracker(repo, grace_period_minutes=5, hard_timeout_minutes=90)


def _make_provider_ids():
    return [ProviderID(type=ProviderType.SPORTRADAR, id="12345")]


class TestRegisterPrematchEvents:
    async def test_registers_new_prematch_event(self, tracker, repo):
        now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        new_ids = await tracker.register_prematch_events(
            prematch_feed=[
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "England Premier League",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now,
        )
        assert new_ids == ["99001"]
        event = await repo.get_by_betpawa_id("99001")
        assert event is not None
        assert event.status == EventStatus.PREMATCH

    async def test_skips_already_registered_event(self, tracker, repo):
        now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        feed = [
            {
                "betpawa_event_id": "99001",
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "competition": "England Premier League",
                "country": "England",
                "scheduled_kickoff": kickoff,
                "provider_ids": _make_provider_ids(),
            }
        ]
        await tracker.register_prematch_events(feed, now=now)
        new_ids = await tracker.register_prematch_events(feed, now=now)
        assert new_ids == []


class TestCheckTransitions:
    async def test_prematch_to_live_on_time(self, tracker, repo):
        # Register prematch event
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now_register = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_register,
        )

        # Check transitions with event in live feed
        now_live = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)
        live_provider_ids = {(ProviderType.SPORTRADAR, "12345")}
        transitions = await tracker.check_transitions(live_provider_ids, now=now_live)

        assert len(transitions) == 1
        t = transitions[0]
        assert t["betpawa_event_id"] == "99001"
        assert t["old_status"] == EventStatus.PREMATCH
        assert t["new_status"] == EventStatus.LIVE

        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.LIVE
        assert event.transition_delay_sec == 60  # 1 min after kickoff

    async def test_prematch_to_late(self, tracker, repo):
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now_register = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_register,
        )

        # 6 minutes past kickoff, not in live feed
        now_late = kickoff + timedelta(minutes=6)
        transitions = await tracker.check_transitions(set(), now=now_late)

        assert len(transitions) == 1
        assert transitions[0]["new_status"] == EventStatus.LATE

    async def test_late_to_live(self, tracker, repo):
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now_reg = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_reg,
        )

        # First: go LATE
        now_late = kickoff + timedelta(minutes=6)
        await tracker.check_transitions(set(), now=now_late)

        # Then: go LIVE
        now_live = kickoff + timedelta(minutes=12)
        live_ids = {(ProviderType.SPORTRADAR, "12345")}
        transitions = await tracker.check_transitions(live_ids, now=now_live)

        assert len(transitions) == 1
        assert transitions[0]["old_status"] == EventStatus.LATE
        assert transitions[0]["new_status"] == EventStatus.LIVE

        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.LIVE
        assert event.transition_delay_sec == 720  # 12 min

    async def test_late_to_never_live(self, tracker, repo):
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now_reg = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_reg,
        )

        # Go LATE first
        now_late = kickoff + timedelta(minutes=6)
        await tracker.check_transitions(set(), now=now_late)

        # Hard timeout
        now_timeout = kickoff + timedelta(minutes=91)
        transitions = await tracker.check_transitions(set(), now=now_timeout)

        assert len(transitions) == 1
        assert transitions[0]["new_status"] == EventStatus.NEVER_LIVE


class TestDetectRemoved:
    async def test_prematch_removed(self, tracker, repo):
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now,
        )

        # Event no longer in prematch feed, before kickoff
        now_check = datetime(2026, 4, 15, 14, 0, tzinfo=UTC)
        removed = await tracker.detect_removed(
            current_prematch_ids=set(), now=now_check
        )
        assert len(removed) == 1
        assert removed[0]["betpawa_event_id"] == "99001"

    async def test_not_removed_if_still_in_feed(self, tracker, repo):
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now,
        )

        now_check = datetime(2026, 4, 15, 14, 0, tzinfo=UTC)
        removed = await tracker.detect_removed(
            current_prematch_ids={"99001"}, now=now_check
        )
        assert len(removed) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_tracker.py -v
```

Expected: FAIL — `ImportError: cannot import name 'EventLifecycleTracker'`

- [ ] **Step 3: Implement lifecycle tracker**

Replace the full contents of `src/live_coverage_bot/core/tracker.py`:

```python
"""Event lifecycle tracker — manages state transitions for prematch events."""

import logging
from datetime import datetime
from typing import Any

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent

logger = logging.getLogger(__name__)


class EventLifecycleTracker:
    """Tracks events through their prematch-to-live lifecycle using SQLite."""

    def __init__(
        self,
        repo: EventRepository,
        grace_period_minutes: int = 5,
        hard_timeout_minutes: int = 90,
    ) -> None:
        self._repo = repo
        self._grace_period_minutes = grace_period_minutes
        self._hard_timeout_minutes = hard_timeout_minutes

    async def register_prematch_events(
        self,
        prematch_feed: list[dict[str, Any]],
        now: datetime,
    ) -> list[str]:
        """Register new prematch events from the feed. Returns list of new event IDs."""
        new_ids: list[str] = []
        for item in prematch_feed:
            bp_id = item["betpawa_event_id"]
            existing = await self._repo.get_by_betpawa_id(bp_id)
            if existing is not None:
                continue

            event = TrackedEvent(
                betpawa_event_id=bp_id,
                home_team=item["home_team"],
                away_team=item["away_team"],
                competition=item["competition"],
                country=item.get("country"),
                scheduled_kickoff=item["scheduled_kickoff"],
                status=EventStatus.PREMATCH,
                provider_ids=item["provider_ids"],
                first_seen_prematch=now,
            )
            await self._repo.insert_event(event)
            new_ids.append(bp_id)
            logger.debug("Registered prematch event: %s %s vs %s", bp_id, event.home_team, event.away_team)

        return new_ids

    async def check_transitions(
        self,
        live_provider_ids: set[tuple[ProviderType, str]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Check all active events for state transitions. Returns list of transitions."""
        active_events = await self._repo.get_active_events()
        transitions: list[dict[str, Any]] = []

        for event in active_events:
            assert event.id is not None
            event_live_keys = {(pid.type, pid.id) for pid in event.provider_ids}
            is_in_live_feed = bool(event_live_keys & live_provider_ids)

            transition = await self._evaluate_transition(event, is_in_live_feed, now)
            if transition:
                transitions.append(transition)

        return transitions

    async def detect_removed(
        self,
        current_prematch_ids: set[str],
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Detect PREMATCH events that disappeared from the feed before kickoff."""
        active_events = await self._repo.get_active_events()
        removed: list[dict[str, Any]] = []

        for event in active_events:
            assert event.id is not None
            if event.status != EventStatus.PREMATCH:
                continue
            if now >= event.scheduled_kickoff:
                continue  # Past kickoff — handled by check_transitions
            if event.betpawa_event_id in current_prematch_ids:
                continue  # Still in feed

            await self._repo.update_status(event.id, EventStatus.REMOVED, updated_at=now)
            await self._repo.insert_state_change(
                event_id=event.id,
                old_status=EventStatus.PREMATCH,
                new_status=EventStatus.REMOVED,
                changed_at=now,
                details="Disappeared from prematch feed before kickoff",
            )
            removed.append({
                "betpawa_event_id": event.betpawa_event_id,
                "event": event,
                "old_status": EventStatus.PREMATCH,
                "new_status": EventStatus.REMOVED,
            })
            logger.info(
                "Event removed: %s %s vs %s",
                event.betpawa_event_id, event.home_team, event.away_team,
            )

        return removed

    async def _evaluate_transition(
        self,
        event: TrackedEvent,
        is_in_live_feed: bool,
        now: datetime,
    ) -> dict[str, Any] | None:
        """Evaluate a single event for state transition."""
        assert event.id is not None
        elapsed_sec = (now - event.scheduled_kickoff).total_seconds()
        elapsed_min = elapsed_sec / 60
        old_status = event.status

        # PREMATCH → LIVE (happy path)
        if event.status == EventStatus.PREMATCH and is_in_live_feed:
            delay_sec = max(0, int(elapsed_sec))
            await self._repo.update_status(event.id, EventStatus.LIVE, updated_at=now)
            await self._repo.update_live_fields(event.id, first_seen_live=now, transition_delay_sec=delay_sec)
            await self._repo.insert_state_change(
                event.id, old_status, EventStatus.LIVE, now,
                details=f"Went live ({delay_sec // 60}min after kickoff)" if delay_sec > 0 else "Went live on time",
            )
            return {
                "betpawa_event_id": event.betpawa_event_id,
                "event": event,
                "old_status": old_status,
                "new_status": EventStatus.LIVE,
                "delay_sec": delay_sec,
            }

        # PREMATCH → LATE
        if event.status == EventStatus.PREMATCH and not is_in_live_feed:
            if elapsed_min >= self._grace_period_minutes:
                await self._repo.update_status(event.id, EventStatus.LATE, updated_at=now)
                await self._repo.insert_state_change(
                    event.id, old_status, EventStatus.LATE, now,
                    details=f"{int(elapsed_min)}min past kickoff",
                )
                return {
                    "betpawa_event_id": event.betpawa_event_id,
                    "event": event,
                    "old_status": old_status,
                    "new_status": EventStatus.LATE,
                }

        # LATE → LIVE
        if event.status == EventStatus.LATE and is_in_live_feed:
            delay_sec = max(0, int(elapsed_sec))
            await self._repo.update_status(event.id, EventStatus.LIVE, updated_at=now)
            await self._repo.update_live_fields(event.id, first_seen_live=now, transition_delay_sec=delay_sec)
            await self._repo.insert_state_change(
                event.id, old_status, EventStatus.LIVE, now,
                details=f"Went live (delay: {delay_sec // 60}min)",
            )
            return {
                "betpawa_event_id": event.betpawa_event_id,
                "event": event,
                "old_status": old_status,
                "new_status": EventStatus.LIVE,
                "delay_sec": delay_sec,
            }

        # LATE → NEVER_LIVE
        if event.status == EventStatus.LATE and not is_in_live_feed:
            if elapsed_min >= self._hard_timeout_minutes:
                await self._repo.update_status(event.id, EventStatus.NEVER_LIVE, updated_at=now)
                await self._repo.insert_state_change(
                    event.id, old_status, EventStatus.NEVER_LIVE, now,
                    details=f"Timed out after {int(elapsed_min)}min",
                )
                return {
                    "betpawa_event_id": event.betpawa_event_id,
                    "event": event,
                    "old_status": old_status,
                    "new_status": EventStatus.NEVER_LIVE,
                }

        return None
```

- [ ] **Step 4: Update core/__init__.py to export tracker**

Replace the full contents of `src/live_coverage_bot/core/__init__.py`:

```python
"""Core business logic for the live coverage bot."""

from live_coverage_bot.core.loop import MonitoringLoop
from live_coverage_bot.core.tracker import EventLifecycleTracker

__all__ = [
    "EventLifecycleTracker",
    "MonitoringLoop",
]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_tracker.py -v
```

Expected: All 8 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/core/tracker.py src/live_coverage_bot/core/__init__.py tests/test_tracker.py
git commit -m "feat: event lifecycle tracker with state transitions and SQLite persistence"
```

---

### Task 8: Weekly Report Generator

**Files:**
- Create: `src/live_coverage_bot/core/reporter.py`
- Create: `tests/test_reporter.py`

- [ ] **Step 1: Write failing tests for reporter**

Create `tests/test_reporter.py`:

```python
"""Tests for weekly report generator."""

import csv
import io
from datetime import UTC, datetime

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent


@pytest.fixture
def repo(db: Database) -> EventRepository:
    return EventRepository(db)


@pytest.fixture
def reporter(repo: EventRepository) -> WeeklyReporter:
    return WeeklyReporter(repo, week_starts="tuesday")


async def _insert_events(repo: EventRepository):
    """Insert a mix of events for testing."""
    base = datetime(2026, 4, 14, tzinfo=UTC)
    pids_sr = [ProviderID(type=ProviderType.SPORTRADAR, id="100")]
    pids_gs = [ProviderID(type=ProviderType.GENIUSSPORTS, id="200")]

    events = [
        # On-time events (LIVE)
        TrackedEvent(
            betpawa_event_id="1", home_team="A", away_team="B",
            competition="EPL", country="England",
            scheduled_kickoff=base, status=EventStatus.LIVE,
            provider_ids=pids_sr, first_seen_prematch=base,
            first_seen_live=base, transition_delay_sec=30,
        ),
        TrackedEvent(
            betpawa_event_id="2", home_team="C", away_team="D",
            competition="EPL", country="England",
            scheduled_kickoff=base, status=EventStatus.LIVE,
            provider_ids=pids_sr, first_seen_prematch=base,
            first_seen_live=base, transition_delay_sec=60,
        ),
        # Late event
        TrackedEvent(
            betpawa_event_id="3", home_team="E", away_team="F",
            competition="LaLiga", country="Spain",
            scheduled_kickoff=base, status=EventStatus.LIVE,
            provider_ids=pids_gs, first_seen_prematch=base,
            first_seen_live=base, transition_delay_sec=600,
        ),
        # Never live
        TrackedEvent(
            betpawa_event_id="4", home_team="G", away_team="H",
            competition="NPFL", country="Nigeria",
            scheduled_kickoff=base, status=EventStatus.NEVER_LIVE,
            provider_ids=pids_gs, first_seen_prematch=base,
        ),
        # Removed
        TrackedEvent(
            betpawa_event_id="5", home_team="I", away_team="J",
            competition="EPL", country="England",
            scheduled_kickoff=base, status=EventStatus.REMOVED,
            provider_ids=pids_sr, first_seen_prematch=base,
        ),
    ]
    for e in events:
        await repo.insert_event(e)


class TestWeeklyReporter:
    async def test_generate_slack_summary(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        assert "Total prematch events tracked: 5" in summary
        assert "SPORTRADAR" in summary
        assert "GENIUSSPORTS" in summary

    async def test_generate_csv_content(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        csv_content = await reporter.generate_csv(start, end)
        reader = csv.DictReader(io.StringIO(csv_content))
        rows = list(reader)
        assert len(rows) == 5
        assert "betpawa_event_id" in rows[0]
        assert "status" in rows[0]

    async def test_compute_report_period_tuesday_week(self, reporter):
        # Tuesday April 14, 2026 08:00 UTC — report covers previous Tue-Mon
        report_time = datetime(2026, 4, 14, 8, 0, tzinfo=UTC)
        start, end = reporter.compute_report_period(report_time)
        # Previous Tuesday = Apr 7, Monday = Apr 13
        assert start.day == 7
        assert end.day == 13

    async def test_empty_report(self, reporter, repo):
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        assert "Total prematch events tracked: 0" in summary
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_reporter.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'live_coverage_bot.core.reporter'`

- [ ] **Step 3: Implement weekly reporter**

Create `src/live_coverage_bot/core/reporter.py`:

```python
"""Weekly report generator for prematch-to-live event tracking."""

import csv
import io
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta

from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent

logger = logging.getLogger(__name__)


class WeeklyReporter:
    """Generates weekly summary reports from tracked event data."""

    WEEKDAY_MAP = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }

    def __init__(self, repo: EventRepository, week_starts: str = "tuesday") -> None:
        self._repo = repo
        self._week_starts = week_starts.lower()

    def compute_report_period(self, report_time: datetime) -> tuple[datetime, datetime]:
        """Compute the Tue–Mon period ending before report_time."""
        target_weekday = self.WEEKDAY_MAP[self._week_starts]
        days_since_start = (report_time.weekday() - target_weekday) % 7
        # End of period = day before report's week start
        period_end_date = (report_time - timedelta(days=days_since_start)).date()
        period_start_date = period_end_date - timedelta(days=6)
        start = datetime(
            period_start_date.year, period_start_date.month, period_start_date.day,
            tzinfo=UTC,
        )
        end = datetime(
            period_end_date.year, period_end_date.month, period_end_date.day,
            23, 59, 59, tzinfo=UTC,
        )
        return start, end

    async def generate_slack_summary(
        self, start: datetime, end: datetime
    ) -> str:
        """Generate the Slack-formatted weekly summary."""
        events = await self._repo.get_events_in_date_range(start, end)
        total = len(events)

        on_time = [e for e in events if e.status == EventStatus.LIVE and (e.transition_delay_sec or 0) < 300]
        late = [e for e in events if e.status == EventStatus.LIVE and (e.transition_delay_sec or 0) >= 300]
        never_live = [e for e in events if e.status == EventStatus.NEVER_LIVE]
        removed = [e for e in events if e.status == EventStatus.REMOVED]

        def pct(count: int) -> str:
            return f"{count / total * 100:.1f}%" if total > 0 else "0%"

        start_str = start.strftime("%b %d")
        end_str = end.strftime("%b %d, %Y")
        lines = [
            f"\U0001f4ca Weekly Report \u2014 {start_str}\u2013{end_str} (Tue\u2013Mon)",
            "",
            f"Total prematch events tracked: {total}",
            f"\u2705 Went live on time: {len(on_time)} ({pct(len(on_time))})",
            f"\U0001f7e1 Went live late: {len(late)} ({pct(len(late))})",
            f"\U0001f534 Never went live: {len(never_live)} ({pct(len(never_live))})",
            f"\U0001f5d1\ufe0f Removed before kickoff: {len(removed)} ({pct(len(removed))})",
        ]

        # Delay stats for late events
        if late:
            delays = [e.transition_delay_sec for e in late if e.transition_delay_sec]
            if delays:
                avg_delay = sum(delays) / len(delays) / 60
                worst = max(delays) / 60
                worst_event = next(e for e in late if e.transition_delay_sec == max(delays))
                lines.append("")
                lines.append(f"Avg delay (late events): {avg_delay:.1f} min")
                lines.append(
                    f"Worst delay: {int(worst)} min \u2014 "
                    f"{worst_event.home_team} vs {worst_event.away_team} "
                    f"({worst_event.scheduled_kickoff.strftime('%b %d')})"
                )

        # Provider breakdown
        lines.append("")
        lines.append("By provider:")
        provider_stats = self._compute_provider_stats(events)
        for provider_type, stats in provider_stats.items():
            on_time_pct = f"{stats['on_time'] / stats['total'] * 100:.0f}%" if stats["total"] > 0 else "0%"
            avg_str = f"{stats['avg_delay']:.1f}min" if stats["avg_delay"] > 0 else "n/a"
            lines.append(
                f"  {provider_type}: {stats['total']} events, "
                f"{on_time_pct} on time, avg delay {avg_str}"
            )

        # Top competitions with issues
        problem_events = late + never_live
        if problem_events:
            comp_counts: Counter[str] = Counter()
            for e in problem_events:
                comp_counts[e.competition] += 1

            lines.append("")
            lines.append("Top competitions with most issues:")
            for i, (comp, count) in enumerate(comp_counts.most_common(5), 1):
                comp_late = sum(1 for e in late if e.competition == comp)
                comp_never = sum(1 for e in never_live if e.competition == comp)
                parts = []
                if comp_late:
                    parts.append(f"{comp_late} late")
                if comp_never:
                    parts.append(f"{comp_never} never live")
                lines.append(f"  {i}. {comp} \u2014 {', '.join(parts)}")

        return "\n".join(lines)

    async def generate_csv(self, start: datetime, end: datetime) -> str:
        """Generate CSV content for the detailed weekly report."""
        events = await self._repo.get_events_in_date_range(start, end)

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "date", "betpawa_event_id", "home_team", "away_team",
                "competition", "country", "provider_type", "provider_id",
                "scheduled_kickoff", "status", "first_seen_live",
                "transition_delay_sec",
            ],
        )
        writer.writeheader()

        for event in events:
            primary_provider = event.provider_ids[0] if event.provider_ids else None
            writer.writerow({
                "date": event.scheduled_kickoff.strftime("%Y-%m-%d"),
                "betpawa_event_id": event.betpawa_event_id,
                "home_team": event.home_team,
                "away_team": event.away_team,
                "competition": event.competition,
                "country": event.country or "",
                "provider_type": primary_provider.type.value if primary_provider else "",
                "provider_id": primary_provider.id if primary_provider else "",
                "scheduled_kickoff": event.scheduled_kickoff.isoformat(),
                "status": event.status.value,
                "first_seen_live": event.first_seen_live.isoformat() if event.first_seen_live else "",
                "transition_delay_sec": event.transition_delay_sec if event.transition_delay_sec is not None else "",
            })

        return output.getvalue()

    def _compute_provider_stats(self, events: list[TrackedEvent]) -> dict[str, dict]:
        """Compute per-provider statistics."""
        stats: dict[str, dict] = {}
        for event in events:
            for pid in event.provider_ids:
                ptype = pid.type.value
                if ptype not in stats:
                    stats[ptype] = {"total": 0, "on_time": 0, "delays": []}
                stats[ptype]["total"] += 1
                if event.status == EventStatus.LIVE:
                    delay = event.transition_delay_sec or 0
                    if delay < 300:
                        stats[ptype]["on_time"] += 1
                    else:
                        stats[ptype]["delays"].append(delay)

        for ptype in stats:
            delays = stats[ptype]["delays"]
            stats[ptype]["avg_delay"] = (sum(delays) / len(delays) / 60) if delays else 0
            del stats[ptype]["delays"]

        return stats
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_reporter.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/core/reporter.py tests/test_reporter.py
git commit -m "feat: weekly report generator with Slack summary and CSV export"
```

---

### Task 9: Adapt BetPawa Client

**Files:**
- Modify: `src/live_coverage_bot/clients/betpawa.py`
- Modify: `src/live_coverage_bot/clients/models.py`

The BetPawa client needs minor adjustments: `get_upcoming_events` should return data in a format the tracker can consume (dicts with the fields it expects), and we need to expose provider IDs from live events as a flat set for matching.

- [ ] **Step 1: Add helper to extract live provider ID set from live events**

Add this method at the end of the `BetPawaClient` class in `src/live_coverage_bot/clients/betpawa.py` (before the `close` method):

```python
    @staticmethod
    def build_live_provider_set(events: list[LiveEvent]) -> set[tuple[ProviderType, str]]:
        """Build a set of (ProviderType, id) tuples from live events for matching."""
        result: set[tuple[ProviderType, str]] = set()
        for event in events:
            for pid in event.provider_ids:
                result.add((pid.type, pid.id))
        return result
```

Also add the import for `ProviderType` if not already present (it is — line 14).

- [ ] **Step 2: Add helper to convert UpcomingEvent list to tracker feed format**

Add this method after `build_live_provider_set`:

```python
    @staticmethod
    def upcoming_to_tracker_feed(events: list[UpcomingEvent]) -> list[dict[str, Any]]:
        """Convert UpcomingEvent list to the dict format expected by the tracker."""
        return [
            {
                "betpawa_event_id": event.event_id,
                "home_team": event.home_team,
                "away_team": event.away_team,
                "competition": event.competition_name,
                "country": event.country_name,
                "scheduled_kickoff": event.start_time,
                "provider_ids": event.provider_ids,
            }
            for event in events
        ]
```

- [ ] **Step 3: Verify the existing tests still pass (if any), then run the full suite**

```bash
python -m pytest -v
```

Expected: All existing tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/live_coverage_bot/clients/betpawa.py
git commit -m "feat: add helper methods to BetPawa client for tracker integration"
```

---

### Task 10: Monitoring Loop Rewrite

**Files:**
- Modify: `src/live_coverage_bot/core/loop.py`
- Create: `tests/test_loop.py`

- [ ] **Step 1: Write failing tests for the new monitoring loop**

Create `tests/test_loop.py`:

```python
"""Tests for the monitoring loop."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType, UpcomingEvent
from live_coverage_bot.config.models import (
    BetPawaConfig,
    DatabaseConfig,
    PollingConfig,
    ReportingConfig,
    Settings,
    SlackConfig,
    ThresholdConfig,
)
from live_coverage_bot.core.loop import MonitoringLoop
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus


@pytest.fixture
def settings():
    return Settings(
        polling=PollingConfig(live_interval_seconds=1, prematch_interval_seconds=5),
        thresholds=ThresholdConfig(grace_period_minutes=5, hard_timeout_minutes=90),
        betpawa=BetPawaConfig(),
        slack=SlackConfig(bot_token="xoxb-test", channel_id="C123"),
        database=DatabaseConfig(path=":memory:"),
        reporting=ReportingConfig(),
        _env_file=None,
    )


class TestPollCycleLogic:
    async def test_new_prematch_event_gets_registered(self, db, settings):
        repo = EventRepository(db)
        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo

        from live_coverage_bot.core.tracker import EventLifecycleTracker
        loop._tracker = EventLifecycleTracker(
            repo,
            grace_period_minutes=settings.thresholds.grace_period_minutes,
            hard_timeout_minutes=settings.thresholds.hard_timeout_minutes,
        )

        # Mock BetPawa client
        mock_betpawa = AsyncMock()
        mock_betpawa.get_upcoming_events.return_value = [
            UpcomingEvent(
                event_id="99001",
                home_team="Arsenal",
                away_team="Chelsea",
                competition_name="EPL",
                country_name="England",
                start_time=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
                provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
            )
        ]
        mock_betpawa.get_live_events.return_value = []

        mock_slack = AsyncMock()

        now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await loop._poll_cycle(mock_betpawa, mock_slack, now=now, force_prematch=True)

        event = await repo.get_by_betpawa_id("99001")
        assert event is not None
        assert event.status == EventStatus.PREMATCH

    async def test_late_event_triggers_slack_alert(self, db, settings):
        repo = EventRepository(db)
        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo

        from live_coverage_bot.core.tracker import EventLifecycleTracker
        loop._tracker = EventLifecycleTracker(
            repo,
            grace_period_minutes=settings.thresholds.grace_period_minutes,
            hard_timeout_minutes=settings.thresholds.hard_timeout_minutes,
        )

        # Register a prematch event first
        mock_betpawa = AsyncMock()
        mock_betpawa.get_upcoming_events.return_value = [
            UpcomingEvent(
                event_id="99001",
                home_team="Arsenal",
                away_team="Chelsea",
                competition_name="EPL",
                country_name="England",
                start_time=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
                provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
            )
        ]
        mock_betpawa.get_live_events.return_value = []

        mock_slack = AsyncMock()
        mock_slack.post_alert.return_value = "1234.5678"

        # Register prematch
        now_reg = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await loop._poll_cycle(mock_betpawa, mock_slack, now=now_reg, force_prematch=True)

        # Trigger LATE (6min past kickoff, not in live feed)
        now_late = datetime(2026, 4, 15, 15, 6, tzinfo=UTC)
        mock_betpawa.get_live_events.return_value = []
        await loop._poll_cycle(mock_betpawa, mock_slack, now=now_late)

        mock_slack.post_alert.assert_called_once()
        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.LATE
        assert event.slack_message_ts == "1234.5678"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_loop.py -v
```

Expected: FAIL — the current loop.py has incompatible code.

- [ ] **Step 3: Implement the new monitoring loop**

Replace the full contents of `src/live_coverage_bot/core/loop.py`:

```python
"""Monitoring loop orchestration for prematch-to-live event tracking."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from live_coverage_bot.clients.betpawa import BetPawaClient, BetPawaError
from live_coverage_bot.clients.slack import SlackClient, SlackError
from live_coverage_bot.config.models import Settings
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.core.tracker import EventLifecycleTracker
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus

logger = logging.getLogger(__name__)


class MonitoringLoop:
    """Orchestrates prematch-to-live event monitoring."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._db: Database | None = None
        self._repo: EventRepository | None = None
        self._tracker: EventLifecycleTracker | None = None
        self._prematch_cycle_counter = 0
        self._last_report_date: datetime | None = None

    async def run(self) -> None:
        """Run the monitoring loop until interrupted."""
        # Initialize database
        db_path = self._settings.database.path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = Database(db_path)
        await self._db.initialize()
        self._repo = EventRepository(self._db)
        self._tracker = EventLifecycleTracker(
            self._repo,
            grace_period_minutes=self._settings.thresholds.grace_period_minutes,
            hard_timeout_minutes=self._settings.thresholds.hard_timeout_minutes,
        )

        async with (
            BetPawaClient(self._settings.betpawa) as betpawa,
            SlackClient(self._settings.slack) as slack,
        ):
            logger.info(
                "Monitoring loop started (live: %ds, prematch: %ds)",
                self._settings.polling.live_interval_seconds,
                self._settings.polling.prematch_interval_seconds,
            )

            while True:
                try:
                    now = datetime.now(tz=UTC)
                    await self._poll_cycle(betpawa, slack, now=now)
                    await self._check_weekly_report(slack, now)
                    await self._cleanup_old_events(now)
                except Exception as e:
                    logger.exception("Unexpected error in poll cycle: %s", e)

                await asyncio.sleep(self._settings.polling.live_interval_seconds)

        await self._db.close()

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

        # Determine if we should fetch prematch this cycle
        prematch_ratio = (
            self._settings.polling.prematch_interval_seconds
            // self._settings.polling.live_interval_seconds
        )
        self._prematch_cycle_counter += 1
        should_fetch_prematch = force_prematch or (self._prematch_cycle_counter >= prematch_ratio)

        # Fetch live events
        try:
            live_events = await betpawa.get_live_events()
        except BetPawaError as e:
            logger.warning("BetPawa live fetch failed, skipping cycle: %s", e)
            return

        live_provider_set = BetPawaClient.build_live_provider_set(live_events)

        # Fetch prematch events if due
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

                # Detect removed events
                current_prematch_ids = {e.event_id for e in upcoming}
                removed = await self._tracker.detect_removed(current_prematch_ids, now=now)
                if removed:
                    logger.info("Detected %d removed prematch events", len(removed))

            except BetPawaError as e:
                logger.warning("BetPawa prematch fetch failed: %s", e)

        # Check transitions for all active events
        transitions = await self._tracker.check_transitions(live_provider_set, now=now)

        # Handle Slack alerts for transitions
        for t in transitions:
            await self._handle_transition(slack, t, now)

        # Log cycle summary
        active = await self._repo.get_active_events()
        prematch_count = sum(1 for e in active if e.status == EventStatus.PREMATCH)
        late_count = sum(1 for e in active if e.status == EventStatus.LATE)
        logger.info(
            "Cycle: %d live events, %d prematch tracked, %d late | %d transitions",
            len(live_events), prematch_count, late_count, len(transitions),
        )

    async def _handle_transition(
        self, slack: SlackClient, transition: dict, now: datetime
    ) -> None:
        """Send Slack alerts/updates for a state transition."""
        assert self._repo is not None
        event = transition["event"]
        old_status = transition["old_status"]
        new_status = transition["new_status"]

        # Refresh event from DB to get updated fields
        refreshed = await self._repo.get_by_betpawa_id(event.betpawa_event_id)
        if refreshed is None:
            return

        try:
            if old_status == EventStatus.PREMATCH and new_status == EventStatus.LATE:
                # First alert — post new parent message
                ts = await slack.post_alert(refreshed, now)
                assert refreshed.id is not None
                await self._repo.update_slack_ts(refreshed.id, ts)
                logger.info(
                    "Alert sent: %s %s vs %s (LATE)",
                    refreshed.betpawa_event_id, refreshed.home_team, refreshed.away_team,
                )

            elif new_status in (EventStatus.LIVE, EventStatus.NEVER_LIVE):
                if refreshed.slack_message_ts:
                    # Update parent message + post thread reply
                    await slack.update_message(refreshed.slack_message_ts, refreshed, now)
                    delay_sec = transition.get("delay_sec")
                    if new_status == EventStatus.LIVE and delay_sec is not None:
                        detail = f"delay: {delay_sec // 60}min"
                    elif new_status == EventStatus.NEVER_LIVE:
                        detail = "timed out"
                    else:
                        detail = None
                    reply_text = slack.format_thread_reply(
                        old_status, new_status, now, detail
                    )
                    await slack.post_thread_reply(refreshed.slack_message_ts, reply_text)
                    logger.info(
                        "Updated alert: %s %s vs %s (%s → %s)",
                        refreshed.betpawa_event_id, refreshed.home_team,
                        refreshed.away_team, old_status, new_status,
                    )

        except SlackError as e:
            logger.warning("Slack operation failed for %s: %s", refreshed.betpawa_event_id, e)

    async def _check_weekly_report(self, slack: SlackClient, now: datetime) -> None:
        """Check if it's time to generate the weekly report."""
        assert self._repo is not None
        cfg = self._settings.reporting
        target_weekday = WeeklyReporter.WEEKDAY_MAP.get(cfg.day.lower())
        if target_weekday is None:
            return

        if now.weekday() != target_weekday:
            return

        hour, minute = map(int, cfg.time.split(":"))
        if now.hour != hour or now.minute < minute:
            return

        # Avoid duplicate reports on the same day
        if self._last_report_date and self._last_report_date.date() == now.date():
            return

        reporter = WeeklyReporter(self._repo, week_starts=cfg.week_starts)
        start, end = reporter.compute_report_period(now)

        try:
            # Post Slack summary
            summary = await reporter.generate_slack_summary(start, end)
            await slack.post_summary(summary)

            # Write CSV file
            csv_content = await reporter.generate_csv(start, end)
            output_dir = Path(cfg.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            csv_path = output_dir / f"weekly-{now.strftime('%Y-%m-%d')}.csv"
            csv_path.write_text(csv_content, encoding="utf-8")

            self._last_report_date = now
            logger.info("Weekly report generated: %s", csv_path)

        except Exception as e:
            logger.error("Failed to generate weekly report: %s", e)

    async def _cleanup_old_events(self, now: datetime) -> None:
        """Delete events older than retention period. Runs once per day."""
        assert self._repo is not None
        retention_days = self._settings.database.retention_days
        cutoff = now - timedelta(days=retention_days)
        deleted = await self._repo.delete_events_before(cutoff)
        if deleted > 0:
            logger.info("Cleaned up %d old events (older than %d days)", deleted, retention_days)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_loop.py -v
```

Expected: All 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/core/loop.py tests/test_loop.py
git commit -m "feat: rewrite monitoring loop for prematch-to-live lifecycle tracking"
```

---

### Task 11: Update Entry Point and Config Files

**Files:**
- Modify: `src/live_coverage_bot/__main__.py`
- Modify: `config.yaml`
- Modify: `config.example.yaml`

- [ ] **Step 1: Rewrite __main__.py**

Replace the full contents of `src/live_coverage_bot/__main__.py`:

```python
"""Entry point for running Live Coverage Bot as a module."""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from live_coverage_bot.config import load_config
from live_coverage_bot.core.loop import MonitoringLoop
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository


def main() -> int:
    """Run the live coverage bot."""
    parser = argparse.ArgumentParser(description="BetPawa Prematch-to-Live Monitor")
    parser.add_argument(
        "--generate-report",
        action="store_true",
        help="Generate weekly report and exit",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)

    try:
        settings = load_config(args.config)
    except Exception as e:
        logger.error("Failed to load settings: %s", e)
        return 1

    if args.generate_report:
        return asyncio.run(_generate_report(settings, logger))

    logger.info(
        "Starting BetPawa Prematch-to-Live Monitor v2 (live: %ds, prematch: %ds)",
        settings.polling.live_interval_seconds,
        settings.polling.prematch_interval_seconds,
    )

    loop = MonitoringLoop(settings)
    try:
        asyncio.run(loop.run())
    except KeyboardInterrupt:
        logger.info("Shutdown requested, exiting")

    return 0


async def _generate_report(settings, logger) -> int:
    """Generate a weekly report on demand."""
    db = Database(settings.database.path)
    await db.initialize()
    repo = EventRepository(db)
    reporter = WeeklyReporter(repo, week_starts=settings.reporting.week_starts)

    now = datetime.now(tz=UTC)
    start, end = reporter.compute_report_period(now)

    summary = await reporter.generate_slack_summary(start, end)
    print(summary)

    csv_content = await reporter.generate_csv(start, end)
    output_dir = Path(settings.reporting.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"weekly-{now.strftime('%Y-%m-%d')}.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    logger.info("CSV report written to %s", csv_path)

    await db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Rewrite config.yaml**

Replace the full contents of `config.yaml`:

```yaml
# BetPawa Prematch-to-Live Monitor Configuration

polling:
  live_interval_seconds: 30
  prematch_interval_seconds: 150  # 2.5 minutes
  prematch_lookahead_hours: 3

thresholds:
  grace_period_minutes: 5
  hard_timeout_minutes: 90

betpawa:
  base_url: "https://www.betpawa.ng/api/sportsbook/v3"
  brand: "betpawa-nigeria"
  language: "en"
  device_type: "web"

slack:
  bot_token: ""  # xoxb-... (REQUIRED)
  channel_id: ""  # C... (REQUIRED)
  summary_channel_id: ""  # Optional, defaults to channel_id

database:
  path: "data/events.db"
  retention_days: 30

reporting:
  day: "tuesday"
  time: "08:00"
  week_starts: "tuesday"
  output_dir: "reports/"
```

- [ ] **Step 3: Rewrite config.example.yaml**

Replace the full contents of `config.example.yaml` with the same content as `config.yaml` above (identical — it serves as the template).

- [ ] **Step 4: Update core/__init__.py with full exports**

Replace the full contents of `src/live_coverage_bot/core/__init__.py`:

```python
"""Core business logic for the live coverage bot."""

from live_coverage_bot.core.loop import MonitoringLoop
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.core.tracker import EventLifecycleTracker

__all__ = [
    "EventLifecycleTracker",
    "MonitoringLoop",
    "WeeklyReporter",
]
```

- [ ] **Step 5: Run the full test suite**

```bash
python -m pytest -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/__main__.py src/live_coverage_bot/core/__init__.py config.yaml config.example.yaml
git commit -m "feat: new entry point with CLI flags and updated config files for v2"
```

---

### Task 12: Update pyproject.toml Description and Run Final Verification

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update project description**

In `pyproject.toml`, update the description:

```toml
description = "BetPawa prematch-to-live event monitor with Slack alerting and weekly reports"
```

- [ ] **Step 2: Add data/ and reports/ to .gitignore**

Create or append to `.gitignore`:

```
data/
reports/
__pycache__/
*.pyc
.env
```

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest -v
```

Expected: All tests PASS.

- [ ] **Step 4: Run ruff linter**

```bash
python -m ruff check src/ tests/
```

Expected: No errors (fix any that appear).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore
git commit -m "chore: update project metadata and add gitignore for v2"
```

---

### Task 13: Integration Smoke Test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/test_integration.py`:

```python
"""Integration smoke test — full cycle with in-memory DB and mocked APIs."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from live_coverage_bot.clients.betpawa import BetPawaClient
from live_coverage_bot.clients.models import (
    LiveEvent,
    ProviderID,
    ProviderType,
    UpcomingEvent,
)
from live_coverage_bot.config.models import (
    BetPawaConfig,
    DatabaseConfig,
    PollingConfig,
    ReportingConfig,
    Settings,
    SlackConfig,
    ThresholdConfig,
)
from live_coverage_bot.core.loop import MonitoringLoop
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus


@pytest.fixture
def settings():
    return Settings(
        polling=PollingConfig(live_interval_seconds=1, prematch_interval_seconds=1),
        thresholds=ThresholdConfig(grace_period_minutes=5, hard_timeout_minutes=90),
        betpawa=BetPawaConfig(),
        slack=SlackConfig(bot_token="xoxb-test", channel_id="C123"),
        database=DatabaseConfig(path=":memory:"),
        reporting=ReportingConfig(),
        _env_file=None,
    )


class TestFullLifecycle:
    """Test a complete event lifecycle: prematch → late → live."""

    async def test_prematch_late_live_cycle(self, db, settings):
        repo = EventRepository(db)
        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo

        from live_coverage_bot.core.tracker import EventLifecycleTracker
        loop._tracker = EventLifecycleTracker(repo, grace_period_minutes=5, hard_timeout_minutes=90)

        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        pids = [ProviderID(type=ProviderType.SPORTRADAR, id="12345")]

        mock_betpawa = AsyncMock()
        mock_slack = AsyncMock()
        mock_slack.post_alert.return_value = "ts_1234"

        # --- Cycle 1: Register prematch ---
        mock_betpawa.get_upcoming_events.return_value = [
            UpcomingEvent(
                event_id="99001", home_team="Arsenal", away_team="Chelsea",
                competition_name="EPL", country_name="England",
                start_time=kickoff, provider_ids=pids,
            )
        ]
        mock_betpawa.get_live_events.return_value = []
        await loop._poll_cycle(mock_betpawa, mock_slack, now=kickoff - timedelta(hours=2), force_prematch=True)

        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.PREMATCH

        # --- Cycle 2: Goes LATE ---
        await loop._poll_cycle(mock_betpawa, mock_slack, now=kickoff + timedelta(minutes=6))
        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.LATE
        assert event.slack_message_ts == "ts_1234"
        mock_slack.post_alert.assert_called_once()

        # --- Cycle 3: Goes LIVE ---
        live_event = LiveEvent(
            event_id="bp:99001", home_team="Arsenal", away_team="Chelsea",
            competition_id="1", competition_name="EPL", country_name="England",
            minute="7", home_score=0, away_score=0, start_time=kickoff,
        )
        live_event._provider_ids_override = pids
        mock_betpawa.get_live_events.return_value = [live_event]

        await loop._poll_cycle(mock_betpawa, mock_slack, now=kickoff + timedelta(minutes=12))
        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.LIVE
        assert event.transition_delay_sec == 720  # 12 minutes
        mock_slack.update_message.assert_called_once()
        mock_slack.post_thread_reply.assert_called_once()

    async def test_weekly_report_generation(self, db, settings):
        """Test that report generates correctly from DB data."""
        repo = EventRepository(db)
        reporter = WeeklyReporter(repo, week_starts="tuesday")

        # Insert test events
        from live_coverage_bot.models.events import TrackedEvent
        kickoff = datetime(2026, 4, 14, 15, 0, tzinfo=UTC)
        await repo.insert_event(TrackedEvent(
            betpawa_event_id="1", home_team="A", away_team="B",
            competition="EPL", country="England",
            scheduled_kickoff=kickoff, status=EventStatus.LIVE,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="100")],
            first_seen_prematch=kickoff, first_seen_live=kickoff,
            transition_delay_sec=60,
        ))

        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        assert "Total prematch events tracked: 1" in summary

        csv_content = await reporter.generate_csv(start, end)
        assert "99001" not in csv_content  # wrong ID
        assert "EPL" in csv_content
```

- [ ] **Step 2: Run the integration test**

```bash
python -m pytest tests/test_integration.py -v
```

Expected: All 2 tests PASS.

- [ ] **Step 3: Run the full test suite one final time**

```bash
python -m pytest -v
```

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: integration smoke test for full prematch→late→live lifecycle"
```
