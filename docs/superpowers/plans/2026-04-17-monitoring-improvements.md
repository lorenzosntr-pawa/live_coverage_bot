# Monitoring Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve transition visibility (REMOVED tracking), market anomaly detail (per-selection shifts, missing markets), and live retention accuracy (multi-snapshot with match state).

**Architecture:** Three interrelated features that share data paths (snapshots, comparisons, Slack messages). Built incrementally — each task produces working software. Database schema changes first, then domain models, then business logic, then Slack formatting.

**Tech Stack:** Python 3.11+, Pydantic v2, aiosqlite, pytest, pytest-asyncio, ruff

**Prerequisite:** Code cleanup plan (2026-04-17-code-cleanup.md) must be completed first.

---

### Task 1: Database Schema Changes

Add all new columns and handle backward compatibility for new snapshot phases.

**Files:**
- Modify: `src/live_coverage_bot/db/connection.py`

- [ ] **Step 1: Add new columns via ALTER TABLE**

In `src/live_coverage_bot/db/connection.py`, after the `SCHEMA_SQL` string definition and before the `Database` class, add a new constant:

```python
MIGRATIONS_SQL = """
-- Feature 1: Enhanced REMOVED tracking
ALTER TABLE events ADD COLUMN removed_at TEXT;
ALTER TABLE events ADD COLUMN pre_removal_market_count INTEGER;

-- Feature 2: Match state on snapshots
ALTER TABLE market_snapshots ADD COLUMN match_state_json TEXT;

-- Feature 3: Snapshot phase on comparisons
ALTER TABLE market_comparisons ADD COLUMN snapshot_phase TEXT;
"""
```

Then in `Database.initialize()`, after `executescript(SCHEMA_SQL)`, add:

```python
# Run additive migrations — each ALTER TABLE is idempotent via
# "duplicate column name" being silently ignored.
for statement in MIGRATIONS_SQL.strip().split(";"):
    stmt = statement.strip()
    if not stmt or stmt.startswith("--"):
        continue
    try:
        await self._conn.execute(stmt)
    except Exception:
        pass  # Column already exists
await self._conn.commit()
```

- [ ] **Step 2: Run existing tests to verify nothing breaks**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_db.py -v`
Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/db/connection.py
git commit -m "feat: add database migrations for REMOVED tracking, match state, and snapshot phases"
```

---

### Task 2: Domain Model Updates

Add `MatchState`, new `SnapshotPhase` values, and `TrackedEvent` fields.

**Files:**
- Modify: `src/live_coverage_bot/models/markets.py`
- Modify: `src/live_coverage_bot/models/events.py`
- Test: `tests/test_markets_models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write tests for new model types**

Add to `tests/test_markets_models.py`:

```python
from live_coverage_bot.models.markets import MatchState, SnapshotPhase


class TestMatchState:
    def test_format_display(self):
        ms = MatchState(minute="33", period="First Half", home_score=1, away_score=0)
        assert ms.display == "\u23f1 33' | First Half | 1-0"

    def test_format_display_no_minute(self):
        ms = MatchState(minute=None, period=None, home_score=None, away_score=None)
        assert ms.display == "\u23f1 ?' | ? | ?-?"

    def test_json_round_trip(self):
        ms = MatchState(minute="10", period="First Half", home_score=0, away_score=0)
        data = ms.model_dump()
        restored = MatchState(**data)
        assert restored == ms


class TestNewSnapshotPhases:
    def test_pre_removal_is_prematch(self):
        assert SnapshotPhase.PRE_REMOVAL.is_prematch

    def test_live_0_is_not_prematch(self):
        assert not SnapshotPhase.LIVE_0.is_prematch

    def test_live_2_is_not_prematch(self):
        assert not SnapshotPhase.LIVE_2.is_prematch

    def test_live_5_is_not_prematch(self):
        assert not SnapshotPhase.LIVE_5.is_prematch

    def test_live_phases(self):
        assert SnapshotPhase.LIVE_0.is_live
        assert SnapshotPhase.LIVE_2.is_live
        assert SnapshotPhase.LIVE_5.is_live
        assert not SnapshotPhase.PREMATCH_60.is_live
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_markets_models.py::TestMatchState tests/test_markets_models.py::TestNewSnapshotPhases -v`
Expected: FAIL — `MatchState` and new phases don't exist yet.

- [ ] **Step 3: Add MatchState and new SnapshotPhase values**

In `src/live_coverage_bot/models/markets.py`:

1. Add `MatchState` after the imports:
```python
class MatchState(BaseModel):
    """Match state at time of snapshot (score, minute, period)."""

    minute: str | None = None
    period: str | None = None
    home_score: int | None = None
    away_score: int | None = None

    @property
    def display(self) -> str:
        """Format as '⏱ 33' | First Half | 1-0'."""
        m = self.minute or "?"
        p = self.period or "?"
        h = self.home_score if self.home_score is not None else "?"
        a = self.away_score if self.away_score is not None else "?"
        return f"\u23f1 {m}' | {p} | {h}-{a}"
```

2. Update `SnapshotPhase`:
```python
class SnapshotPhase(StrEnum):
    """Market snapshot phases for a tracked event."""

    PREMATCH_60 = "PREMATCH_60"
    PREMATCH_15 = "PREMATCH_15"
    PREMATCH_1 = "PREMATCH_1"
    PRE_REMOVAL = "PRE_REMOVAL"
    LIVE = "LIVE"        # Legacy — kept for backward compat with existing DB rows
    LIVE_0 = "LIVE_0"
    LIVE_2 = "LIVE_2"
    LIVE_5 = "LIVE_5"

    @property
    def is_prematch(self) -> bool:
        """True for any prematch phase (including PRE_REMOVAL)."""
        return self in (
            SnapshotPhase.PREMATCH_60,
            SnapshotPhase.PREMATCH_15,
            SnapshotPhase.PREMATCH_1,
            SnapshotPhase.PRE_REMOVAL,
        )

    @property
    def is_live(self) -> bool:
        """True for any live phase."""
        return self in (
            SnapshotPhase.LIVE,
            SnapshotPhase.LIVE_0,
            SnapshotPhase.LIVE_2,
            SnapshotPhase.LIVE_5,
        )
```

3. Add `match_state` to `MarketSnapshot`:
```python
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
    match_state: MatchState | None = None
```

4. Add `snapshot_phase` to `MarketComparison`:
```python
class MarketComparison(BaseModel):
    """Result of diffing a prematch snapshot against a live snapshot."""

    id: int | None = None
    event_id: int
    compared_at: datetime
    prematch_phase: SnapshotPhase
    snapshot_phase: SnapshotPhase | None = None  # Which live snapshot this comparison is for
    markets_added: int
    markets_dropped: int
    markets_kept: int
    retention_pct: float
    dropped_key_markets: list[str]
    max_odds_shift_pct: float
    triggered_alert: bool
    details: ComparisonDetails
```

- [ ] **Step 4: Add removed_at and pre_removal_market_count to TrackedEvent**

In `src/live_coverage_bot/models/events.py`, add fields to `TrackedEvent`:

```python
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
```

Also update `EventStatus.is_terminal` — `REMOVED` is no longer terminal since we keep watching it:
```python
@property
def is_terminal(self) -> bool:
    """Whether this status represents a final state (no further transitions)."""
    return self in (EventStatus.LIVE, EventStatus.NEVER_LIVE)
```

- [ ] **Step 5: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/models/markets.py src/live_coverage_bot/models/events.py tests/test_markets_models.py
git commit -m "feat: add MatchState, new SnapshotPhase values, and TrackedEvent removed fields"
```

---

### Task 3: Match State Parser

Add `parse_match_state()` to the parsers module.

**Files:**
- Modify: `src/live_coverage_bot/clients/parsers.py`
- Test: `tests/test_parsers.py`

- [ ] **Step 1: Write tests for parse_match_state**

Add to `tests/test_parsers.py`:

```python
from live_coverage_bot.clients.parsers import parse_match_state
from live_coverage_bot.models.markets import MatchState


class TestParseMatchState:
    def test_parses_full_match_state(self):
        data = {
            "results": {
                "display": {
                    "minute": "33",
                    "currentPeriod": {"id": "3007", "name": "First Half", "slug": "FIRST_HALF"},
                },
                "participantPeriodResults": [
                    {
                        "participant": {"id": "1", "type": "HOME"},
                        "periodResults": [
                            {"period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"}, "result": "1", "type": "SCORE"},
                        ],
                    },
                    {
                        "participant": {"id": "2", "type": "AWAY"},
                        "periodResults": [
                            {"period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"}, "result": "0", "type": "SCORE"},
                        ],
                    },
                ],
            }
        }
        ms = parse_match_state(data)
        assert ms is not None
        assert ms.minute == "33"
        assert ms.period == "First Half"
        assert ms.home_score == 1
        assert ms.away_score == 0

    def test_returns_none_for_no_results(self):
        ms = parse_match_state({"results": None})
        assert ms is None

    def test_returns_none_for_empty_dict(self):
        ms = parse_match_state({})
        assert ms is None

    def test_partial_match_state(self):
        data = {
            "results": {
                "display": {"minute": "5", "currentPeriod": None},
                "participantPeriodResults": [],
            }
        }
        ms = parse_match_state(data)
        assert ms is not None
        assert ms.minute == "5"
        assert ms.period is None
        assert ms.home_score is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_parsers.py::TestParseMatchState -v`
Expected: FAIL — `parse_match_state` doesn't exist.

- [ ] **Step 3: Implement parse_match_state**

In `src/live_coverage_bot/clients/parsers.py`, add:

```python
from live_coverage_bot.models.markets import MatchState


def parse_match_state(data: dict[str, Any]) -> MatchState | None:
    """Parse match state (minute, period, score) from event data.

    Works with both the full event response and the live events list response.
    """
    results = data.get("results")
    if not results:
        return None

    display = results.get("display") or {}
    minute = display.get("minute")
    current_period = display.get("currentPeriod") or {}
    period = current_period.get("name") if current_period else None

    home_score, away_score = extract_scores(results)

    # Only return MatchState if we have at least some data
    if minute is None and period is None and home_score is None:
        return None

    return MatchState(
        minute=minute,
        period=period,
        home_score=home_score,
        away_score=away_score,
    )
```

- [ ] **Step 4: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/clients/parsers.py tests/test_parsers.py
git commit -m "feat: add parse_match_state to extract score/minute/period from API response"
```

---

### Task 4: Repository Updates

Update repositories to handle new fields: `removed_at`, `pre_removal_market_count`, `match_state`, `snapshot_phase`.

**Files:**
- Modify: `src/live_coverage_bot/db/repository.py`
- Modify: `src/live_coverage_bot/db/market_repository.py`
- Test: `tests/test_db.py`
- Test: `tests/test_market_repository.py`

- [ ] **Step 1: Write tests for new repository methods**

Add to `tests/test_db.py`:

```python
from datetime import UTC, datetime

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent


class TestRemovedFields:
    async def test_update_removed_fields(self, db):
        repo = EventRepository(db)
        event = TrackedEvent(
            betpawa_event_id="300",
            home_team="X",
            away_team="Y",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 18, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        row_id = await repo.insert_event(event)

        removed_at = datetime(2026, 4, 15, 14, 0, tzinfo=UTC)
        await repo.update_removed_fields(row_id, removed_at=removed_at, market_count=56)

        fetched = await repo.get_by_id(row_id)
        assert fetched is not None
        assert fetched.removed_at == removed_at
        assert fetched.pre_removal_market_count == 56
```

Add to `tests/test_market_repository.py`:

```python
from live_coverage_bot.models.markets import MatchState, SnapshotPhase


class TestMatchStateStorage:
    async def test_snapshot_with_match_state(self, db):
        from live_coverage_bot.db.market_repository import MarketRepository
        from live_coverage_bot.db.repository import EventRepository
        from live_coverage_bot.models.events import EventStatus, TrackedEvent
        from live_coverage_bot.models.markets import MarketSnapshot, MatchState
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        event_repo = EventRepository(db)
        market_repo = MarketRepository(db)

        event = TrackedEvent(
            betpawa_event_id="500",
            home_team="A",
            away_team="B",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.LIVE,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        row_id = await event_repo.insert_event(event)

        ms = MatchState(minute="10", period="First Half", home_score=0, away_score=0)
        snapshot = MarketSnapshot(
            event_id=row_id,
            phase=SnapshotPhase.LIVE_0,
            taken_at=datetime(2026, 4, 15, 15, 1, tzinfo=UTC),
            markets=[],
            total_market_count=0,
            total_selection_count=0,
            suspended_count=0,
            match_state=ms,
        )
        await market_repo.insert_snapshot(snapshot)

        fetched = await market_repo.get_snapshots_for_event(row_id)
        assert len(fetched) == 1
        assert fetched[0].match_state is not None
        assert fetched[0].match_state.minute == "10"
        assert fetched[0].match_state.home_score == 0


class TestSnapshotPhaseOnComparison:
    async def test_comparison_stores_snapshot_phase(self, db):
        from live_coverage_bot.db.market_repository import MarketRepository
        from live_coverage_bot.db.repository import EventRepository
        from live_coverage_bot.models.events import EventStatus, TrackedEvent
        from live_coverage_bot.models.markets import (
            ComparisonDetails,
            MarketComparison,
            SnapshotPhase,
        )
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        event_repo = EventRepository(db)
        market_repo = MarketRepository(db)

        event = TrackedEvent(
            betpawa_event_id="600",
            home_team="A",
            away_team="B",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.LIVE,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        row_id = await event_repo.insert_event(event)

        cmp = MarketComparison(
            event_id=row_id,
            compared_at=datetime(2026, 4, 15, 15, 1, tzinfo=UTC),
            prematch_phase=SnapshotPhase.PREMATCH_1,
            snapshot_phase=SnapshotPhase.LIVE_2,
            markets_added=0,
            markets_dropped=5,
            markets_kept=20,
            retention_pct=80.0,
            dropped_key_markets=[],
            max_odds_shift_pct=0,
            triggered_alert=False,
            details=ComparisonDetails(dropped=[], added=[], odds_shifts=[]),
        )
        await market_repo.insert_comparison(cmp)

        fetched = await market_repo.get_comparison_for_event(row_id)
        assert fetched is not None
        assert fetched.snapshot_phase == SnapshotPhase.LIVE_2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_db.py::TestRemovedFields tests/test_market_repository.py::TestMatchStateStorage tests/test_market_repository.py::TestSnapshotPhaseOnComparison -v`
Expected: FAIL.

- [ ] **Step 3: Update EventRepository**

In `src/live_coverage_bot/db/repository.py`:

1. Add new method:
```python
async def update_removed_fields(
    self, event_id: int, removed_at: datetime, market_count: int | None = None
) -> None:
    """Update removal-specific fields."""
    await self._db.execute(
        "UPDATE events SET removed_at = ?, pre_removal_market_count = ?, updated_at = ? WHERE id = ?",
        (removed_at.isoformat(), market_count, removed_at.isoformat(), event_id),
    )
```

2. Update `_row_to_event` to include new fields:
```python
removed_at=_parse_dt(row.get("removed_at")),
pre_removal_market_count=row.get("pre_removal_market_count"),
```

3. Update `insert_event` to include the new columns:
Add `removed_at` and `pre_removal_market_count` to the INSERT statement and values tuple.

- [ ] **Step 4: Update MarketRepository**

In `src/live_coverage_bot/db/market_repository.py`:

1. Update `insert_snapshot` to include `match_state_json`:
```python
async def insert_snapshot(
    self, snapshot: MarketSnapshot, fetch_duration_ms: int | None = None
) -> int:
    match_state_json = (
        json.dumps(snapshot.match_state.model_dump(), separators=JSON_SEPARATORS)
        if snapshot.match_state else None
    )
    cursor = await self._db.execute(
        """INSERT INTO market_snapshots
        (event_id, phase, taken_at, markets_json, total_market_count,
         total_selection_count, suspended_count, fetch_duration_ms, match_state_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            snapshot.event_id,
            snapshot.phase.value,
            snapshot.taken_at.isoformat(),
            snapshot.markets_json,
            snapshot.total_market_count,
            snapshot.total_selection_count,
            snapshot.suspended_count,
            fetch_duration_ms,
            match_state_json,
        ),
    )
    return cursor.lastrowid
```

2. Update `_row_to_snapshot` to deserialize match state:
```python
from live_coverage_bot.models.markets import MatchState

def _row_to_snapshot(self, row: dict) -> MarketSnapshot:
    match_state = None
    match_state_raw = row.get("match_state_json")
    if match_state_raw:
        match_state = MatchState(**json.loads(match_state_raw))

    return MarketSnapshot(
        id=row["id"],
        event_id=row["event_id"],
        phase=SnapshotPhase(row["phase"]),
        taken_at=datetime.fromisoformat(row["taken_at"]),
        markets=MarketSnapshot.markets_from_json(row["markets_json"]),
        total_market_count=row["total_market_count"],
        total_selection_count=row["total_selection_count"],
        suspended_count=row["suspended_count"],
        match_state=match_state,
    )
```

3. Update `insert_comparison` to include `snapshot_phase`:
```python
async def insert_comparison(self, cmp: MarketComparison) -> int:
    cursor = await self._db.execute(
        """INSERT INTO market_comparisons
        (event_id, compared_at, prematch_phase, snapshot_phase, markets_added,
         markets_dropped, markets_kept, retention_pct, dropped_key_markets,
         max_odds_shift_pct, triggered_alert, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            cmp.event_id,
            cmp.compared_at.isoformat(),
            cmp.prematch_phase.value,
            cmp.snapshot_phase.value if cmp.snapshot_phase else None,
            cmp.markets_added,
            cmp.markets_dropped,
            cmp.markets_kept,
            cmp.retention_pct,
            json.dumps(cmp.dropped_key_markets),
            cmp.max_odds_shift_pct,
            1 if cmp.triggered_alert else 0,
            json.dumps(cmp.details.model_dump(), separators=JSON_SEPARATORS),
        ),
    )
    return cursor.lastrowid
```

4. Update `_row_to_comparison` to deserialize `snapshot_phase`:
```python
snapshot_phase=(
    SnapshotPhase(row["snapshot_phase"]) if row.get("snapshot_phase") else None
),
```

5. Update `PREMATCH_PHASES_CLOSEST_FIRST` to include `PRE_REMOVAL`:
```python
PREMATCH_PHASES_CLOSEST_FIRST = (
    SnapshotPhase.PREMATCH_1,
    SnapshotPhase.PRE_REMOVAL,
    SnapshotPhase.PREMATCH_15,
    SnapshotPhase.PREMATCH_60,
)
```

- [ ] **Step 5: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/db/repository.py src/live_coverage_bot/db/market_repository.py src/live_coverage_bot/db/connection.py tests/test_db.py tests/test_market_repository.py
git commit -m "feat: update repositories for match state, removed fields, and snapshot phases"
```

---

### Task 5: Configuration Updates

Add new config options for live snapshot offsets and odds shift display threshold.

**Files:**
- Modify: `src/live_coverage_bot/config/models.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write test for new config options**

Add to `tests/test_config.py`:

```python
from live_coverage_bot.config.models import MarketsConfig


class TestNewMarketConfig:
    def test_default_live_snapshot_offsets(self):
        cfg = MarketsConfig()
        assert cfg.live_snapshot_offsets_minutes == [0, 2, 5]

    def test_default_odds_shift_display_threshold(self):
        cfg = MarketsConfig()
        assert cfg.odds_shift_display_threshold == 5.0

    def test_custom_offsets(self):
        cfg = MarketsConfig(live_snapshot_offsets_minutes=[0, 3, 10])
        assert cfg.live_snapshot_offsets_minutes == [0, 3, 10]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_config.py::TestNewMarketConfig -v`
Expected: FAIL.

- [ ] **Step 3: Add config fields**

In `src/live_coverage_bot/config/models.py`, update `MarketsConfig`:

```python
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
    live_snapshot_offsets_minutes: list[int] = [0, 2, 5]
    odds_shift_display_threshold: float = 5.0
```

- [ ] **Step 4: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/config/models.py tests/test_config.py
git commit -m "feat: add live_snapshot_offsets_minutes and odds_shift_display_threshold config"
```

---

### Task 6: Enhanced REMOVED Event Tracking

Update tracker to capture pre-removal market count, log enriched details, and support REMOVED → PREMATCH recovery.

**Files:**
- Modify: `src/live_coverage_bot/core/tracker.py`
- Modify: `src/live_coverage_bot/core/loop.py`
- Test: `tests/test_tracker.py`

- [ ] **Step 1: Write tests for enhanced removal**

Add to `tests/test_tracker.py`:

```python
class TestEnhancedRemoval:
    async def test_detect_removed_records_removed_at(self, db):
        from live_coverage_bot.db.repository import EventRepository
        from live_coverage_bot.core.tracker import EventLifecycleTracker
        from live_coverage_bot.models.events import EventStatus, TrackedEvent
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo)
        event = TrackedEvent(
            betpawa_event_id="400",
            home_team="E",
            away_team="F",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 18, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="3")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        await repo.insert_event(event)

        now = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        results = await tracker.detect_removed(set(), now=now)
        assert len(results) == 1

        fetched = await repo.get_by_betpawa_id("400")
        assert fetched is not None
        assert fetched.removed_at == now

    async def test_removed_to_prematch_recovery(self, db):
        from live_coverage_bot.db.repository import EventRepository
        from live_coverage_bot.core.tracker import EventLifecycleTracker
        from live_coverage_bot.models.events import EventStatus, TrackedEvent
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo)
        event = TrackedEvent(
            betpawa_event_id="401",
            home_team="G",
            away_team="H",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 18, 0, tzinfo=UTC),
            status=EventStatus.REMOVED,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="4")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            removed_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
        )
        await repo.insert_event(event)

        now = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        current_prematch_ids = {"401"}
        recoveries = await tracker.detect_prematch_recovery(
            current_prematch_ids, now=now
        )
        assert len(recoveries) == 1
        assert recoveries[0].new_status == EventStatus.PREMATCH

        fetched = await repo.get_by_betpawa_id("401")
        assert fetched is not None
        assert fetched.status == EventStatus.PREMATCH
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_tracker.py::TestEnhancedRemoval -v`
Expected: FAIL.

- [ ] **Step 3: Update detect_removed to store removed_at**

In `src/live_coverage_bot/core/tracker.py`, update `detect_removed`:

After `await self._repo.update_status(event.id, EventStatus.REMOVED, updated_at=now)` add:
```python
await self._repo.update_removed_fields(event.id, removed_at=now)
```

Update the details string:
```python
minutes_before = int(minutes_to_kickoff)
details = f"Disappeared from prematch at {now.strftime('%H:%M')} UTC, {minutes_before}min before kickoff"
```

- [ ] **Step 4: Add detect_prematch_recovery method**

In `src/live_coverage_bot/core/tracker.py`, add:

```python
async def detect_prematch_recovery(
    self,
    current_prematch_ids: set[str],
    now: datetime,
) -> list[TransitionResult]:
    """Detect REMOVED events that reappeared in the prematch feed."""
    from datetime import timedelta

    lookback = now - timedelta(hours=24)
    removed_events = await self._repo.get_recent_removed_events(lookback)
    recoveries: list[TransitionResult] = []

    for event in removed_events:
        assert event.id is not None
        if event.betpawa_event_id not in current_prematch_ids:
            continue

        gap_min = 0
        if event.removed_at:
            gap_min = int((now - event.removed_at).total_seconds() / 60)

        await self._repo.update_status(event.id, EventStatus.PREMATCH, updated_at=now)
        details = (
            f"Reappeared in prematch at {now.strftime('%H:%M')} UTC "
            f"after {gap_min}min gap"
        )
        await self._repo.insert_state_change(
            event.id, EventStatus.REMOVED, EventStatus.PREMATCH, now,
            details=details,
        )
        logger.info(
            "Recovered REMOVED to PREMATCH: %s %s vs %s (gap: %dmin)",
            event.betpawa_event_id, event.home_team, event.away_team, gap_min,
        )
        recoveries.append(TransitionResult(
            event=event,
            old_status=EventStatus.REMOVED,
            new_status=EventStatus.PREMATCH,
            details=details,
        ))

    return recoveries
```

- [ ] **Step 5: Update REMOVED→LIVE recovery in check_transitions with enriched details**

In `check_transitions`, update the REMOVED→LIVE recovery block to include gap info:

```python
gap_min = 0
if event.removed_at:
    gap_min = int((now - event.removed_at).total_seconds() / 60)

details = (
    f"Reappeared directly in live at {now.strftime('%H:%M')} UTC "
    f"after {gap_min}min gap"
)
await self._repo.insert_state_change(
    event.id, EventStatus.REMOVED, EventStatus.LIVE, now,
    details=details,
)
transitions.append(TransitionResult(
    event=event,
    old_status=EventStatus.REMOVED,
    new_status=EventStatus.LIVE,
    delay_sec=delay_sec,
    details=details,
))
```

- [ ] **Step 6: Wire up in loop.py _poll_cycle**

In `src/live_coverage_bot/core/loop.py`, after the existing `detect_removed` call in `_poll_cycle`, add the prematch recovery check:

```python
if current_prematch_ids is not None:
    removed = await self._tracker.detect_removed(current_prematch_ids, now=now)
    if removed:
        logger.info("Detected %d removed prematch events", len(removed))

    # Check if any REMOVED events reappeared in prematch
    prematch_recoveries = await self._tracker.detect_prematch_recovery(
        current_prematch_ids, now=now
    )
    for recovery in prematch_recoveries:
        await self._handle_transition(slack, recovery, now)
```

- [ ] **Step 7: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/core/tracker.py src/live_coverage_bot/core/loop.py tests/test_tracker.py
git commit -m "feat: enhanced REMOVED event tracking with prematch recovery and enriched details"
```

---

### Task 7: Multi-Snapshot Live Retention

Update the snapshotter to take LIVE_0, LIVE_2, and LIVE_5 snapshots with match state.

**Files:**
- Modify: `src/live_coverage_bot/core/market_snapshotter.py`
- Modify: `src/live_coverage_bot/core/loop.py`
- Test: `tests/test_market_snapshotter.py`

- [ ] **Step 1: Write tests for follow-up snapshot scheduling**

Add to `tests/test_market_snapshotter.py`:

```python
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from live_coverage_bot.config.models import MarketsConfig
from live_coverage_bot.core.market_snapshotter import MarketSnapshotter
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.models.markets import SnapshotPhase
from live_coverage_bot.clients.models import ProviderID, ProviderType


def _live_event(first_seen_live: datetime) -> TrackedEvent:
    return TrackedEvent(
        id=1,
        betpawa_event_id="700",
        home_team="A",
        away_team="B",
        competition="Test",
        scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
        status=EventStatus.LIVE,
        provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
        first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        first_seen_live=first_seen_live,
    )


class TestLiveFollowUpPhases:
    def test_live_2_due_after_2_minutes(self):
        config = MarketsConfig()
        snapshotter = MarketSnapshotter(
            MagicMock(), MagicMock(), MagicMock(), config
        )
        event = _live_event(first_seen_live=datetime(2026, 4, 15, 15, 0, tzinfo=UTC))
        now = datetime(2026, 4, 15, 15, 2, 30, tzinfo=UTC)

        phases = snapshotter._live_follow_up_phases_due(event, now)
        assert SnapshotPhase.LIVE_2 in phases
        assert SnapshotPhase.LIVE_5 not in phases

    def test_live_5_due_after_5_minutes(self):
        config = MarketsConfig()
        snapshotter = MarketSnapshotter(
            MagicMock(), MagicMock(), MagicMock(), config
        )
        event = _live_event(first_seen_live=datetime(2026, 4, 15, 15, 0, tzinfo=UTC))
        now = datetime(2026, 4, 15, 15, 5, 30, tzinfo=UTC)

        phases = snapshotter._live_follow_up_phases_due(event, now)
        assert SnapshotPhase.LIVE_2 in phases
        assert SnapshotPhase.LIVE_5 in phases

    def test_no_phases_before_2_minutes(self):
        config = MarketsConfig()
        snapshotter = MarketSnapshotter(
            MagicMock(), MagicMock(), MagicMock(), config
        )
        event = _live_event(first_seen_live=datetime(2026, 4, 15, 15, 0, tzinfo=UTC))
        now = datetime(2026, 4, 15, 15, 1, 0, tzinfo=UTC)

        phases = snapshotter._live_follow_up_phases_due(event, now)
        assert len(phases) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_market_snapshotter.py::TestLiveFollowUpPhases -v`
Expected: FAIL.

- [ ] **Step 3: Add _live_follow_up_phases_due and update run_cycle**

In `src/live_coverage_bot/core/market_snapshotter.py`:

1. Add the new method:
```python
def _live_follow_up_phases_due(
    self, event: TrackedEvent, now: datetime
) -> list[SnapshotPhase]:
    """Return follow-up live phases that are due for this event."""
    if event.status != EventStatus.LIVE or event.first_seen_live is None:
        return []

    offsets = self._config.live_snapshot_offsets_minutes
    elapsed_min = (now - event.first_seen_live).total_seconds() / 60.0

    # Map offset -> phase name. Offset 0 is LIVE_0 (taken on transition).
    offset_phase_map = {
        2: SnapshotPhase.LIVE_2,
        5: SnapshotPhase.LIVE_5,
    }

    phases: list[SnapshotPhase] = []
    for offset in offsets:
        if offset == 0:
            continue  # LIVE_0 is taken on transition, not here
        phase = offset_phase_map.get(offset)
        if phase and elapsed_min >= offset:
            phases.append(phase)

    return phases
```

2. Update `run_cycle` to add follow-up live snapshots to the plan:

After the existing prematch loop but before the `live_transition_event_ids` block, add:

```python
# Follow-up live snapshots (LIVE_2, LIVE_5) for events already live
for event in active:
    if event.status != EventStatus.LIVE or event.id is None:
        continue
    taken = await self._markets.phases_taken_for_event(event.id)
    for phase in self._live_follow_up_phases_due(event, now):
        if phase not in taken:
            plan.append((event, phase))
```

3. Update the initial LIVE transition snapshot to use `LIVE_0` instead of `LIVE`:

Replace `SnapshotPhase.LIVE` with `SnapshotPhase.LIVE_0` in the `live_transition_event_ids` block:
```python
if SnapshotPhase.LIVE_0 not in taken:
    plan.append((event, SnapshotPhase.LIVE_0))
```

- [ ] **Step 4: Update _take_snapshot to include match state**

In `_take_snapshot`, after fetching markets, fetch match state and include it:

```python
from live_coverage_bot.clients.parsers import parse_match_state

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

    # Parse match state for live snapshots
    match_state = None
    if phase.is_live:
        try:
            response = await self._betpawa._client.get(f"/events/{event.betpawa_event_id}")
            response.raise_for_status()
            match_state = parse_match_state(response.json())
        except Exception:
            logger.debug("Could not fetch match state for %s", event.betpawa_event_id)

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
        match_state=match_state,
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

- [ ] **Step 5: Update loop.py to handle multi-snapshot comparisons**

In `src/live_coverage_bot/core/loop.py`, update the comparison block (around line 148):

Replace:
```python
for event_id, phase in taken:
    if phase != SnapshotPhase.LIVE:
        continue
    await self._handle_market_comparison(slack, event_id, now)
```

With:
```python
for event_id, phase in taken:
    if not phase.is_live:
        continue
    await self._handle_market_comparison(slack, event_id, phase, now)
```

Update `_handle_market_comparison` signature and logic to accept `phase`:

```python
async def _handle_market_comparison(
    self, slack: SlackClient, event_id: int, phase: SnapshotPhase, now: datetime
) -> None:
```

And when finding the live snapshot, look for the specific phase:
```python
live_snap = next(
    (s for s in reversed(all_snaps) if s.phase == phase), None
)
```

Pass `snapshot_phase=phase` to `compare_snapshots` result (set on the comparison object).

- [ ] **Step 6: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/core/market_snapshotter.py src/live_coverage_bot/core/loop.py tests/test_market_snapshotter.py
git commit -m "feat: multi-snapshot live retention with LIVE_0/LIVE_2/LIVE_5 phases and match state"
```

---

### Task 8: Detailed Market Anomaly Thread Messages

Update Slack formatting with missing markets, per-selection odds shifts, and match state context.

**Files:**
- Modify: `src/live_coverage_bot/clients/slack.py`
- Test: `tests/test_slack.py`

- [ ] **Step 1: Write tests for new detailed thread formatting**

Add to `tests/test_slack.py`:

```python
from live_coverage_bot.models.markets import (
    ComparisonDetails,
    DroppedMarketDetail,
    OddsShiftDetail,
    MarketComparison,
    MatchState,
    SnapshotPhase,
)


class TestDetailedMarketThread:
    def test_format_missing_markets(self, slack_config):
        client = SlackClient(slack_config)
        dropped = [
            DroppedMarketDetail(market_type_id="1", market_type_name="1X2 - FT"),
            DroppedMarketDetail(market_type_id="2", market_type_name="Both Teams To Score - FT"),
            DroppedMarketDetail(market_type_id="3", market_type_name="Correct Score - FT"),
        ]
        key_markets = ["1X2 - FT", "Both Teams To Score - FT"]
        text = client.format_missing_markets(dropped, key_markets)
        assert "1X2 - FT" in text
        assert "KEY" in text
        assert "Correct Score - FT" in text

    def test_format_missing_markets_truncation(self, slack_config):
        client = SlackClient(slack_config)
        dropped = [
            DroppedMarketDetail(market_type_id=str(i), market_type_name=f"Market {i}")
            for i in range(12)
        ]
        key_markets = ["Market 0"]
        text = client.format_missing_markets(dropped, key_markets)
        assert "KEY" in text
        assert "more" in text

    def test_format_odds_shifts(self, slack_config):
        client = SlackClient(slack_config)
        shifts = [
            OddsShiftDetail(
                market_type_name="1X2 - FT", selection_name="Home",
                selection_type_id="1", handicap=None,
                prematch_price=1.50, live_price=1.85, shift_pct=23.3,
            ),
            OddsShiftDetail(
                market_type_name="1X2 - FT", selection_name="Draw",
                selection_type_id="2", handicap=None,
                prematch_price=3.20, live_price=2.90, shift_pct=9.4,
            ),
            OddsShiftDetail(
                market_type_name="BTTS", selection_name="Yes",
                selection_type_id="3", handicap=None,
                prematch_price=1.75, live_price=1.60, shift_pct=8.6,
            ),
        ]
        text = client.format_odds_shifts(shifts, threshold=5.0)
        assert "1X2 - FT" in text
        assert "Home" in text
        assert "1.50" in text
        assert "1.85" in text
        assert "BTTS" in text

    def test_format_odds_shifts_below_threshold(self, slack_config):
        client = SlackClient(slack_config)
        shifts = [
            OddsShiftDetail(
                market_type_name="1X2 - FT", selection_name="Home",
                selection_type_id="1", handicap=None,
                prematch_price=2.00, live_price=2.01, shift_pct=0.5,
            ),
        ]
        text = client.format_odds_shifts(shifts, threshold=5.0)
        assert "No significant odds shifts" in text

    def test_format_snapshot_update(self, slack_config):
        client = SlackClient(slack_config)
        match_state = MatchState(minute="4", period="First Half", home_score=0, away_score=0)
        text = client.format_snapshot_update(
            match_state=match_state,
            phase_label="+2min",
            prev_kept=22,
            curr_kept=31,
            prev_retention=39.0,
            curr_retention=55.0,
            recovered=["Double Chance - FT", "Correct Score - FT"],
            still_missing=["1X2 - FT", "Half Time/Full Time"],
            key_markets=["1X2 - FT"],
        )
        assert "4'" in text
        assert "First Half" in text
        assert "22" in text
        assert "31" in text
        assert "Recovered" in text
        assert "Double Chance - FT" in text
        assert "Still missing" in text
        assert "1X2 - FT" in text
        assert "KEY" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_slack.py::TestDetailedMarketThread -v`
Expected: FAIL.

- [ ] **Step 3: Implement new formatting methods**

In `src/live_coverage_bot/clients/slack.py`, add these methods to `SlackClient`:

```python
def format_missing_markets(
    self,
    dropped: list["DroppedMarketDetail"],
    key_market_names: list[str],
) -> str:
    """Format the missing markets section for a thread reply."""
    from live_coverage_bot.models.markets import DroppedMarketDetail

    if not dropped:
        return ""

    key_set = set(key_market_names)
    key_dropped = [d for d in dropped if d.market_type_name in key_set]
    non_key_dropped = [d for d in dropped if d.market_type_name not in key_set]
    max_non_key = 7

    lines = [f"\u274c Missing markets ({len(dropped)} dropped):"]
    for d in key_dropped:
        lines.append(f"  \u2022 {d.market_type_name} {EMOJI_WARNING} KEY")
    for d in non_key_dropped[:max_non_key]:
        lines.append(f"  \u2022 {d.market_type_name}")
    remaining = len(non_key_dropped) - max_non_key
    if remaining > 0:
        lines.append(f"  ... and {remaining} more")

    return "\n".join(lines)

def format_odds_shifts(
    self,
    shifts: list["OddsShiftDetail"],
    threshold: float = 5.0,
) -> str:
    """Format per-selection odds shifts grouped by market."""
    from collections import defaultdict
    from live_coverage_bot.models.markets import OddsShiftDetail

    significant = [s for s in shifts if abs(s.shift_pct) >= threshold]
    if not significant:
        return f"{EMOJI_CHART} Odds shifts (prematch \u2192 live):\n  No significant odds shifts."

    by_market: dict[str, list[OddsShiftDetail]] = defaultdict(list)
    for s in significant:
        by_market[s.market_type_name].append(s)

    lines = [f"{EMOJI_CHART} Odds shifts (prematch \u2192 live):"]
    for market_name, sels in by_market.items():
        lines.append(f"  {market_name}:")
        for s in sels:
            sign = "+" if s.live_price >= s.prematch_price else ""
            pct = s.shift_pct if s.live_price >= s.prematch_price else -s.shift_pct
            lines.append(
                f"    {s.selection_name}  {s.prematch_price:.2f} \u2192 "
                f"{s.live_price:.2f} ({sign}{pct:.1f}%)"
            )

    return "\n".join(lines)

def format_snapshot_update(
    self,
    match_state: "MatchState | None",
    phase_label: str,
    prev_kept: int,
    curr_kept: int,
    prev_retention: float,
    curr_retention: float,
    recovered: list[str],
    still_missing: list[str],
    key_markets: list[str],
) -> str:
    """Format a follow-up snapshot thread update (LIVE_2, LIVE_5)."""
    from live_coverage_bot.models.markets import MatchState

    lines: list[str] = []
    if match_state:
        lines.append(match_state.display)

    lines.append(
        f"{EMOJI_CHART} Snapshot {phase_label}: "
        f"{prev_kept} \u2192 {curr_kept} markets "
        f"({prev_retention:.0f}% \u2192 {curr_retention:.0f}% retention)"
    )

    key_set = set(key_markets)
    if recovered:
        lines.append(f"{EMOJI_CHECK} Recovered: {', '.join(recovered)}")
    if still_missing:
        tagged = []
        for name in still_missing:
            tag = f" {EMOJI_WARNING} KEY" if name in key_set else ""
            tagged.append(f"{name}{tag}")
        lines.append(f"\u274c Still missing: {', '.join(tagged)}")

    return "\n".join(lines)
```

- [ ] **Step 4: Update format_market_anomaly_parent with new summary**

Replace the `Biggest odds shift` line in `format_market_anomaly_parent`:

```python
def format_market_anomaly_parent(
    self,
    event: TrackedEvent,
    comparison: "MarketComparison",
) -> str:
    """Format a standalone 'market anomaly' parent message for happy-path events."""
    provider_str, competition_line, kickoff_str = self._format_event_header(event)
    total_prematch = comparison.markets_dropped + comparison.markets_kept

    # Count significant shifts
    threshold = 5.0  # Will be passed from config in the loop
    significant_shifts = sum(
        1 for s in comparison.details.odds_shifts if abs(s.shift_pct) >= threshold
    )

    lines = [
        f"{EMOJI_CHART} MARKET ANOMALY {EMOJI_DASH} {event.home_team} vs {event.away_team}",
        f"{EMOJI_CLIPBOARD} {competition_line}",
        f"{EMOJI_CLOCK} Kickoff: {kickoff_str} | Went live on time",
        f"{EMOJI_PLUG} {provider_str}",
        f"{EMOJI_ID} BetPawa ID: {event.betpawa_event_id}",
        "",
        f"{total_prematch} prematch markets \u2192 {comparison.markets_kept} live "
        f"({comparison.retention_pct:.0f}% retention)",
        f"\u274c {comparison.markets_dropped} markets dropped, "
        f"{significant_shifts} selections shifted > 5%",
    ]
    if comparison.dropped_key_markets:
        lines.append(
            f"{EMOJI_WARNING} Key market dropped: "
            f"{', '.join(comparison.dropped_key_markets)}"
        )
    return "\n".join(lines)
```

- [ ] **Step 5: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/clients/slack.py tests/test_slack.py
git commit -m "feat: detailed market anomaly threads with missing markets, per-selection shifts, match state"
```

---

### Task 9: Wire Market Comparison Alert Flow with Multi-Snapshot Updates

Connect the multi-snapshot comparisons to the new Slack formatting in the monitoring loop.

**Files:**
- Modify: `src/live_coverage_bot/core/loop.py`
- Modify: `src/live_coverage_bot/core/market_comparator.py`
- Test: `tests/test_loop.py`

- [ ] **Step 1: Update compare_snapshots to set snapshot_phase**

In `src/live_coverage_bot/core/market_comparator.py`, add `snapshot_phase` parameter:

```python
def compare_snapshots(
    prematch: MarketSnapshot,
    live: MarketSnapshot,
    config: MarketsConfig,
    now: datetime,
    snapshot_phase: SnapshotPhase | None = None,
) -> MarketComparison:
```

And in the return statement, add:
```python
snapshot_phase=snapshot_phase,
```

- [ ] **Step 2: Update _handle_market_comparison for multi-snapshot flow**

In `src/live_coverage_bot/core/loop.py`, rewrite `_handle_market_comparison`:

```python
async def _handle_market_comparison(
    self, slack: SlackClient, event_id: int, phase: SnapshotPhase, now: datetime
) -> None:
    """Run comparison for a live snapshot; route to Slack with detailed formatting."""
    assert self._repo is not None
    assert self._market_repo is not None

    prematch_snap = await self._market_repo.get_latest_prematch_snapshot(event_id)
    if prematch_snap is None:
        logger.info(
            "No prematch snapshot for event %d \u2014 skipping comparison", event_id,
        )
        return

    all_snaps = await self._market_repo.get_snapshots_for_event(event_id)
    live_snap = next(
        (s for s in reversed(all_snaps) if s.phase == phase), None
    )
    if live_snap is None:
        return

    cmp = compare_snapshots(
        prematch_snap, live_snap, self._settings.markets, now=now,
        snapshot_phase=phase,
    )

    try:
        await self._market_repo.insert_comparison(cmp)
    except Exception as e:
        logger.warning("Market comparison insert failed for event %d: %s", event_id, e)
        return

    event = await self._repo.get_by_id(event_id)
    if event is None:
        return

    try:
        if phase == SnapshotPhase.LIVE_0:
            await self._handle_initial_comparison(slack, event, cmp, live_snap, now)
        elif phase in (SnapshotPhase.LIVE_2, SnapshotPhase.LIVE_5):
            await self._handle_followup_comparison(
                slack, event, cmp, live_snap, phase, now
            )
    except SlackError as e:
        logger.warning(
            "Slack market alert failed for %s: %s", event.betpawa_event_id, e
        )

async def _handle_initial_comparison(
    self,
    slack: SlackClient,
    event: "TrackedEvent",
    cmp: "MarketComparison",
    live_snap: "MarketSnapshot",
    now: datetime,
) -> None:
    """Handle the LIVE_0 comparison — post initial alert if triggered."""
    if not cmp.triggered_alert:
        return

    config = self._settings.markets

    if event.slack_message_ts:
        # Post detailed thread replies
        missing_text = slack.format_missing_markets(
            cmp.details.dropped, config.key_markets
        )
        shifts_text = slack.format_odds_shifts(
            cmp.details.odds_shifts, config.odds_shift_display_threshold
        )
        match_header = live_snap.match_state.display if live_snap.match_state else ""
        thread_text = "\n".join(filter(None, [match_header, missing_text, shifts_text]))
        await slack.post_thread_reply(event.slack_message_ts, thread_text)
    else:
        parent_text = slack.format_market_anomaly_parent(event, cmp)
        ts = await slack.post_market_anomaly_alert(event, parent_text)
        assert event.id is not None
        await self._repo.update_slack_ts(event.id, ts)

        # Post detail in thread
        missing_text = slack.format_missing_markets(
            cmp.details.dropped, config.key_markets
        )
        shifts_text = slack.format_odds_shifts(
            cmp.details.odds_shifts, config.odds_shift_display_threshold
        )
        match_header = live_snap.match_state.display if live_snap.match_state else ""
        thread_text = "\n".join(filter(None, [match_header, missing_text, shifts_text]))
        await slack.post_thread_reply(ts, thread_text)

    logger.info("Posted market anomaly alert for %s", event.betpawa_event_id)

async def _handle_followup_comparison(
    self,
    slack: SlackClient,
    event: "TrackedEvent",
    cmp: "MarketComparison",
    live_snap: "MarketSnapshot",
    phase: "SnapshotPhase",
    now: datetime,
) -> None:
    """Handle LIVE_2/LIVE_5 comparison — post update thread if event has an alert."""
    # Find the LIVE_0 comparison to compute delta
    assert self._market_repo is not None
    prev_cmp = None
    all_cmps = await self._market_repo.get_comparisons_for_event(event.id)
    # Find the most recent comparison before this one
    for c in reversed(all_cmps):
        if c.snapshot_phase != phase and c.snapshot_phase is not None:
            prev_cmp = c
            break

    if event.slack_message_ts is None:
        # No existing thread — check if this snapshot triggers a new alert
        if cmp.triggered_alert:
            parent_text = slack.format_market_anomaly_parent(event, cmp)
            ts = await slack.post_market_anomaly_alert(event, parent_text)
            assert event.id is not None
            await self._repo.update_slack_ts(event.id, ts)
        return

    # Post update in existing thread
    prev_kept = prev_cmp.markets_kept if prev_cmp else 0
    prev_retention = prev_cmp.retention_pct if prev_cmp else 0.0

    # Compute recovered and still-missing
    prev_dropped_names = (
        {d.market_type_name for d in prev_cmp.details.dropped}
        if prev_cmp else set()
    )
    curr_dropped_names = {d.market_type_name for d in cmp.details.dropped}

    recovered = sorted(prev_dropped_names - curr_dropped_names)
    still_missing = sorted(curr_dropped_names)

    offset_min = {
        SnapshotPhase.LIVE_2: 2,
        SnapshotPhase.LIVE_5: 5,
    }

    update_text = slack.format_snapshot_update(
        match_state=live_snap.match_state,
        phase_label=f"+{offset_min.get(phase, '?')}min",
        prev_kept=prev_kept,
        curr_kept=cmp.markets_kept,
        prev_retention=prev_retention,
        curr_retention=cmp.retention_pct,
        recovered=recovered,
        still_missing=still_missing,
        key_markets=self._settings.markets.key_markets,
    )
    await slack.post_thread_reply(event.slack_message_ts, update_text)
    logger.info(
        "Posted %s update for %s", phase.value, event.betpawa_event_id
    )
```

- [ ] **Step 3: Add get_comparisons_for_event to MarketRepository**

In `src/live_coverage_bot/db/market_repository.py`, add:

```python
async def get_comparisons_for_event(self, event_id: int) -> list[MarketComparison]:
    """Fetch all comparisons for an event, ordered by compared_at."""
    rows = await self._db.fetch_all(
        "SELECT * FROM market_comparisons WHERE event_id = ? ORDER BY compared_at",
        (event_id,),
    )
    return [self._row_to_comparison(r) for r in rows]
```

- [ ] **Step 4: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/core/loop.py src/live_coverage_bot/core/market_comparator.py src/live_coverage_bot/db/market_repository.py tests/test_loop.py
git commit -m "feat: wire multi-snapshot comparison flow with detailed Slack alerts and follow-up updates"
```

---

### Task 10: Update Weekly Report to Use Final Snapshot

**Files:**
- Modify: `src/live_coverage_bot/core/market_reporter.py`
- Modify: `src/live_coverage_bot/db/market_repository.py`
- Test: `tests/test_market_reporter.py`

- [ ] **Step 1: Write test for final-snapshot preference**

Add to `tests/test_market_reporter.py`:

```python
class TestFinalSnapshotPreference:
    async def test_aggregate_uses_live_5_over_live_0(self, db):
        from live_coverage_bot.db.market_repository import MarketRepository
        from live_coverage_bot.db.repository import EventRepository
        from live_coverage_bot.core.market_reporter import MarketReporter
        from live_coverage_bot.models.events import EventStatus, TrackedEvent
        from live_coverage_bot.models.markets import (
            ComparisonDetails,
            MarketComparison,
            SnapshotPhase,
        )
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        event_repo = EventRepository(db)
        market_repo = MarketRepository(db)
        reporter = MarketReporter(event_repo, market_repo)

        event = TrackedEvent(
            betpawa_event_id="800",
            home_team="A", away_team="B", competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.LIVE,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        row_id = await event_repo.insert_event(event)

        details = ComparisonDetails(dropped=[], added=[], odds_shifts=[])

        # Insert LIVE_0 comparison with 50% retention
        cmp_0 = MarketComparison(
            event_id=row_id,
            compared_at=datetime(2026, 4, 15, 15, 1, tzinfo=UTC),
            prematch_phase=SnapshotPhase.PREMATCH_1,
            snapshot_phase=SnapshotPhase.LIVE_0,
            markets_added=0, markets_dropped=5, markets_kept=5,
            retention_pct=50.0,
            dropped_key_markets=[], max_odds_shift_pct=0,
            triggered_alert=False, details=details,
        )
        await market_repo.insert_comparison(cmp_0)

        # Insert LIVE_5 comparison with 80% retention
        cmp_5 = MarketComparison(
            event_id=row_id,
            compared_at=datetime(2026, 4, 15, 15, 6, tzinfo=UTC),
            prematch_phase=SnapshotPhase.PREMATCH_1,
            snapshot_phase=SnapshotPhase.LIVE_5,
            markets_added=0, markets_dropped=2, markets_kept=8,
            retention_pct=80.0,
            dropped_key_markets=[], max_odds_shift_pct=0,
            triggered_alert=False, details=details,
        )
        await market_repo.insert_comparison(cmp_5)

        start = datetime(2026, 4, 15, 0, 0, tzinfo=UTC)
        end = datetime(2026, 4, 15, 23, 59, tzinfo=UTC)
        summary = await reporter.generate_markets_summary_block(start, end)

        # Should use LIVE_5 retention (80%) not LIVE_0 (50%)
        assert "80" in summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_market_reporter.py::TestFinalSnapshotPreference -v`
Expected: FAIL.

- [ ] **Step 3: Add get_best_comparison_for_event to MarketRepository**

In `src/live_coverage_bot/db/market_repository.py`:

```python
LIVE_PHASE_PREFERENCE = (
    SnapshotPhase.LIVE_5,
    SnapshotPhase.LIVE_2,
    SnapshotPhase.LIVE_0,
    SnapshotPhase.LIVE,  # Legacy
)


async def get_best_comparison_for_event(
    self, event_id: int
) -> MarketComparison | None:
    """Return the best (latest live phase) comparison for weekly reporting."""
    for phase in LIVE_PHASE_PREFERENCE:
        row = await self._db.fetch_one(
            "SELECT * FROM market_comparisons WHERE event_id = ? AND snapshot_phase = ?",
            (event_id, phase.value),
        )
        if row is not None:
            return self._row_to_comparison(row)
    # Fallback: any comparison for this event (backward compat)
    return await self.get_comparison_for_event(event_id)
```

- [ ] **Step 4: Update MarketReporter to use best comparison**

In `src/live_coverage_bot/core/market_reporter.py`, update `generate_aggregate_csv` and `generate_markets_summary_block` to use `get_best_comparison_for_event` instead of using all comparisons directly. 

For `generate_markets_summary_block`, replace:
```python
comparisons = await self._markets.get_comparisons_in_date_range(start, end)
```
With logic that deduplicates to one comparison per event using the best available:
```python
all_comparisons = await self._markets.get_comparisons_in_date_range(start, end)
# Deduplicate: one comparison per event, prefer LIVE_5 > LIVE_2 > LIVE_0
seen_events: set[int] = set()
comparisons: list[MarketComparison] = []
for cmp in all_comparisons:
    if cmp.event_id in seen_events:
        continue
    best = await self._markets.get_best_comparison_for_event(cmp.event_id)
    if best:
        comparisons.append(best)
        seen_events.add(cmp.event_id)
```

- [ ] **Step 5: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/core/market_reporter.py src/live_coverage_bot/db/market_repository.py tests/test_market_reporter.py
git commit -m "feat: weekly report uses LIVE_5 retention over LIVE_0, with fallback chain"
```

---

### Task 11: Pre-Removal Market Snapshot

Take a market snapshot before marking an event as REMOVED.

**Files:**
- Modify: `src/live_coverage_bot/core/loop.py`
- Test: `tests/test_loop.py`

- [ ] **Step 1: Wire pre-removal snapshot in _poll_cycle**

In `src/live_coverage_bot/core/loop.py`, after detecting removed events, take snapshots for them:

```python
if current_prematch_ids is not None:
    removed = await self._tracker.detect_removed(current_prematch_ids, now=now)
    if removed:
        logger.info("Detected %d removed prematch events", len(removed))
        # Take PRE_REMOVAL snapshots
        for r in removed:
            event = r.event
            if event.id is None:
                continue
            taken_phases = await self._market_repo.phases_taken_for_event(event.id)
            has_recent_prematch = any(p.is_prematch for p in taken_phases)
            if not has_recent_prematch:
                try:
                    snapshot_result = await snapshotter._take_snapshot(
                        event, SnapshotPhase.PRE_REMOVAL, now
                    )
                    if snapshot_result:
                        _, _ = snapshot_result
                        # Update event with market count
                        markets = await self._betpawa_ref.get_event_markets(
                            event.betpawa_event_id
                        )
                        await self._repo.update_removed_fields(
                            event.id, removed_at=now, market_count=len(markets)
                        )
                except Exception as e:
                    logger.warning(
                        "Pre-removal snapshot failed for %s: %s",
                        event.betpawa_event_id, e,
                    )
```

Wait — this approach needs access to the BetPawaClient reference. Let me adjust. The snapshotter already has the betpawa client, and the pre-removal snapshot fetches the market count. Let's simplify:

Actually, `detect_removed` already ran. We can update `removed_at` with the count from the snapshot. Let me restructure this more cleanly:

In the detect_removed loop handler, after `detect_removed` returns, for each removed event:

```python
if current_prematch_ids is not None:
    removed = await self._tracker.detect_removed(current_prematch_ids, now=now)
    for r in removed:
        if r.event.id is None:
            continue
        # Take pre-removal snapshot if no prematch snapshot exists
        taken_phases = await self._market_repo.phases_taken_for_event(r.event.id)
        if not any(p.is_prematch for p in taken_phases):
            result = await snapshotter._take_snapshot(
                r.event, SnapshotPhase.PRE_REMOVAL, now
            )
            if result:
                snap = await self._market_repo.get_snapshots_for_event(r.event.id)
                pre_removal = next(
                    (s for s in snap if s.phase == SnapshotPhase.PRE_REMOVAL), None
                )
                if pre_removal:
                    await self._repo.update_removed_fields(
                        r.event.id,
                        removed_at=now,
                        market_count=pre_removal.total_market_count,
                    )
        else:
            # Use existing prematch snapshot market count
            latest_pre = await self._market_repo.get_latest_prematch_snapshot(r.event.id)
            if latest_pre:
                await self._repo.update_removed_fields(
                    r.event.id,
                    removed_at=now,
                    market_count=latest_pre.total_market_count,
                )
    if removed:
        logger.info("Detected %d removed prematch events", len(removed))
```

- [ ] **Step 2: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/core/loop.py
git commit -m "feat: take PRE_REMOVAL market snapshot and store market count on removal"
```

---

### Task 12: Slack Messages for REMOVED Events

Add market count to removal alerts and recovery thread replies.

**Files:**
- Modify: `src/live_coverage_bot/clients/slack.py`
- Modify: `src/live_coverage_bot/core/loop.py`
- Test: `tests/test_slack.py`

- [ ] **Step 1: Write tests for removal formatting**

Add to `tests/test_slack.py`:

```python
class TestRemovedFormatting:
    def test_format_removed_message(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.REMOVED
        text = client.format_parent_message(sample_event)
        assert "REMOVED" in text

    def test_format_removal_with_market_count(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.REMOVED
        sample_event.pre_removal_market_count = 56
        text = client.format_parent_message(sample_event)
        assert "56 markets" in text

    def test_format_recovery_thread_reply(self, slack_config):
        client = SlackClient(slack_config)
        ms = MatchState(minute="1", period="First Half", home_score=0, away_score=0)
        text = client.format_recovery_reply(
            feed="live",
            gap_minutes=15,
            pre_removal_markets=56,
            current_markets=48,
            match_state=ms,
        )
        assert "Reappeared" in text
        assert "live" in text
        assert "15" in text
        assert "56" in text
        assert "48" in text
        assert "1'" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_slack.py::TestRemovedFormatting -v`
Expected: FAIL.

- [ ] **Step 3: Add REMOVED status to format_parent_message**

In `src/live_coverage_bot/clients/slack.py`, in `format_parent_message`, add a block for REMOVED status before the default return:

```python
if event.status == EventStatus.REMOVED:
    removed_delay = ""
    if event.scheduled_kickoff > (now or datetime.now(tz=UTC)):
        mins_before = int(
            (event.scheduled_kickoff - (now or datetime.now(tz=UTC))).total_seconds() / 60
        )
        removed_delay = f"Removed {mins_before}min before kickoff"
    else:
        removed_delay = "Removed after kickoff"

    lines = [
        f"\U0001f5d1\ufe0f REMOVED {EMOJI_DASH} {event.home_team} vs {event.away_team}",
        f"{EMOJI_CLIPBOARD} {competition_line}",
        f"{EMOJI_CLOCK} Kickoff: {kickoff_str} | {removed_delay}",
        f"{EMOJI_PLUG} {provider_str}",
        f"{EMOJI_ID} BetPawa ID: {event.betpawa_event_id}",
    ]
    if event.pre_removal_market_count is not None:
        lines.append(f"\nHad {event.pre_removal_market_count} markets at time of removal")
    return "\n".join(lines)
```

- [ ] **Step 4: Add format_recovery_reply method**

```python
def format_recovery_reply(
    self,
    feed: str,
    gap_minutes: int,
    pre_removal_markets: int | None,
    current_markets: int | None,
    match_state: "MatchState | None" = None,
) -> str:
    """Format a thread reply when a REMOVED event reappears."""
    from live_coverage_bot.models.markets import MatchState

    lines = [
        f"{datetime.now(tz=UTC).strftime('%H:%M')} {EMOJI_DASH} "
        f"\u267b\ufe0f Reappeared in {feed} after {gap_minutes}min",
    ]
    if pre_removal_markets is not None and current_markets is not None:
        lines.append(
            f"Markets: {pre_removal_markets} before removal \u2192 {current_markets} now"
        )
    if match_state:
        lines.append(match_state.display)

    return "\n".join(lines)
```

- [ ] **Step 5: Wire recovery replies in loop.py _handle_transition**

In the `_handle_transition` method, add handling for REMOVED→PREMATCH and enhance REMOVED→LIVE:

```python
elif old_status == EventStatus.REMOVED and new_status in (EventStatus.PREMATCH, EventStatus.LIVE):
    if refreshed.slack_message_ts:
        feed = "prematch" if new_status == EventStatus.PREMATCH else "live"
        gap_min = 0
        if refreshed.removed_at:
            gap_min = int((now - refreshed.removed_at).total_seconds() / 60)
        
        # Get current market count
        current_markets = None
        try:
            # We can get this from the latest snapshot if one was just taken
            if self._market_repo and refreshed.id:
                snaps = await self._market_repo.get_snapshots_for_event(refreshed.id)
                if snaps:
                    current_markets = snaps[-1].total_market_count
        except Exception:
            pass

        reply = slack.format_recovery_reply(
            feed=feed,
            gap_minutes=gap_min,
            pre_removal_markets=refreshed.pre_removal_market_count,
            current_markets=current_markets,
        )
        await slack.post_thread_reply(refreshed.slack_message_ts, reply)
```

- [ ] **Step 6: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 7: Run ruff linter**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m ruff check src/`
Expected: No new errors.

- [ ] **Step 8: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/clients/slack.py src/live_coverage_bot/core/loop.py tests/test_slack.py
git commit -m "feat: Slack messages for REMOVED events with market count and recovery thread replies"
```
