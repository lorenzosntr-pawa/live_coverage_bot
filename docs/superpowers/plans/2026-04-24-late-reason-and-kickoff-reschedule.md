# Late Reason Classification & Kickoff Reschedule Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify why events are late (match delayed vs coverage late vs kickoff rescheduled), store the reason in the DB, show it in Slack alerts, and detect kickoff reschedules from the prematch feed to revert false LATE alerts.

**Architecture:** Two new DB columns (`late_reason`, `live_minute`) on `events`. The tracker's `_evaluate_transition` gets two new data maps (live events, prematch events) to classify late reasons at transition time and detect kickoff reschedules. Slack formatting and weekly reports are extended to surface the new data.

**Tech Stack:** Python 3.11+, aiosqlite, httpx, pydantic, pytest-asyncio

---

### Task 1: Config — Add `coverage_late_minute_threshold`

**Files:**
- Modify: `src/live_coverage_bot/config/models.py:15-20`
- Modify: `config.yaml:8-10`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_config.py`, add a test for the new threshold field:

```python
class TestCoverageLateThreshold:
    def test_default_coverage_late_minute_threshold(self):
        from live_coverage_bot.config.models import ThresholdConfig
        config = ThresholdConfig()
        assert config.coverage_late_minute_threshold == 5

    def test_custom_coverage_late_minute_threshold(self):
        from live_coverage_bot.config.models import ThresholdConfig
        config = ThresholdConfig(coverage_late_minute_threshold=10)
        assert config.coverage_late_minute_threshold == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::TestCoverageLateThreshold -v`
Expected: FAIL — `ThresholdConfig` has no field `coverage_late_minute_threshold`

- [ ] **Step 3: Add the field to ThresholdConfig**

In `src/live_coverage_bot/config/models.py`, add to the `ThresholdConfig` class (after line 20):

```python
class ThresholdConfig(BaseModel):
    """Event lifecycle threshold configuration."""

    grace_period_minutes: int = 5
    hard_timeout_minutes: int = 90
    on_time_threshold_seconds: int = 300
    coverage_late_minute_threshold: int = 5
```

- [ ] **Step 4: Add to config.yaml**

In `config.yaml`, add under `thresholds:` section (after `hard_timeout_minutes: 90`):

```yaml
thresholds:
  grace_period_minutes: 5
  hard_timeout_minutes: 90
  coverage_late_minute_threshold: 5
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py::TestCoverageLateThreshold -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All existing tests still pass

- [ ] **Step 7: Commit**

```bash
git add src/live_coverage_bot/config/models.py config.yaml tests/test_config.py
git commit -m "feat: add coverage_late_minute_threshold config field"
```

---

### Task 2: Models — Add `late_reason` and `live_minute` fields

**Files:**
- Modify: `src/live_coverage_bot/models/events.py:29-49` (TrackedEvent)
- Modify: `src/live_coverage_bot/models/events.py:77-84` (TransitionResult)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_models.py`, add tests for the new fields:

```python
class TestLateReasonFields:
    def test_tracked_event_late_reason_default_none(self):
        from live_coverage_bot.models.events import TrackedEvent, EventStatus
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        event = TrackedEvent(
            betpawa_event_id="1", home_team="A", away_team="B",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        assert event.late_reason is None
        assert event.live_minute is None

    def test_tracked_event_late_reason_set(self):
        from live_coverage_bot.models.events import TrackedEvent, EventStatus
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        event = TrackedEvent(
            betpawa_event_id="1", home_team="A", away_team="B",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            late_reason="COVERAGE_LATE",
            live_minute=23,
        )
        assert event.late_reason == "COVERAGE_LATE"
        assert event.live_minute == 23

    def test_transition_result_live_minute(self):
        from live_coverage_bot.models.events import TransitionResult, EventStatus, TrackedEvent
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        event = TrackedEvent(
            betpawa_event_id="1", home_team="A", away_team="B",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        result = TransitionResult(
            event=event, old_status=EventStatus.LATE,
            new_status=EventStatus.LIVE, live_minute=12,
        )
        assert result.live_minute == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py::TestLateReasonFields -v`
Expected: FAIL — `TrackedEvent` has no field `late_reason` / `live_minute`

- [ ] **Step 3: Add fields to TrackedEvent and TransitionResult**

In `src/live_coverage_bot/models/events.py`, add to `TrackedEvent` (after `pre_removal_market_count`):

```python
class TrackedEvent(BaseModel):
    """An event being tracked through its prematch-to-live lifecycle."""

    id: int | None = None
    betpawa_event_id: str
    home_team: str
    away_team: str
    competition: str
    competition_id: str = ""
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
    late_reason: str | None = None
    live_minute: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
```

Add `live_minute` to `TransitionResult`:

```python
class TransitionResult(BaseModel):
    """Result of a single event state transition."""

    event: TrackedEvent
    old_status: EventStatus
    new_status: EventStatus
    delay_sec: int | None = None
    live_minute: int | None = None
    details: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_models.py::TestLateReasonFields -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/models/events.py tests/test_models.py
git commit -m "feat: add late_reason and live_minute fields to event models"
```

---

### Task 3: DB Migration — Add `late_reason` and `live_minute` columns

**Files:**
- Modify: `src/live_coverage_bot/db/connection.py:82-95`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_db.py`, add a test in `TestDatabase`:

```python
class TestLateReasonColumns:
    async def test_events_has_late_reason_column(self, db: Database):
        columns = await db.fetch_all("PRAGMA table_info(events)")
        col_names = {row["name"] for row in columns}
        assert "late_reason" in col_names

    async def test_events_has_live_minute_column(self, db: Database):
        columns = await db.fetch_all("PRAGMA table_info(events)")
        col_names = {row["name"] for row in columns}
        assert "live_minute" in col_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db.py::TestLateReasonColumns -v`
Expected: FAIL — columns don't exist

- [ ] **Step 3: Add migration SQL**

In `src/live_coverage_bot/db/connection.py`, append to `MIGRATIONS_SQL` (after the `competition_id` migration):

```python
MIGRATIONS_SQL = """
-- Feature 1: Enhanced REMOVED tracking
ALTER TABLE events ADD COLUMN removed_at TEXT;
ALTER TABLE events ADD COLUMN pre_removal_market_count INTEGER;

-- Feature 2: Match state on snapshots
ALTER TABLE market_snapshots ADD COLUMN match_state_json TEXT;

-- Feature 3: Snapshot phase on comparisons
ALTER TABLE market_comparisons ADD COLUMN snapshot_phase TEXT;

-- Feature 4: Competition ID for league filtering
ALTER TABLE events ADD COLUMN competition_id TEXT DEFAULT '';

-- Feature 5: Late reason classification
ALTER TABLE events ADD COLUMN late_reason TEXT;
ALTER TABLE events ADD COLUMN live_minute INTEGER;
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db.py::TestLateReasonColumns -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/db/connection.py tests/test_db.py
git commit -m "feat: add late_reason and live_minute columns via migration"
```

---

### Task 4: Repository — Extend `update_live_fields` and add `update_scheduled_kickoff`

**Files:**
- Modify: `src/live_coverage_bot/db/repository.py:94-110` (update_live_fields)
- Modify: `src/live_coverage_bot/db/repository.py:218-239` (_row_to_event)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_db.py`, add to `TestEventRepository`:

```python
class TestLateReasonRepository:
    @pytest.fixture
    def repo(self, db: Database) -> EventRepository:
        return EventRepository(db)

    def _make_event(self, **overrides) -> TrackedEvent:
        defaults = dict(
            betpawa_event_id="99001",
            home_team="Arsenal", away_team="Chelsea",
            competition="EPL", country="England",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        defaults.update(overrides)
        return TrackedEvent(**defaults)

    async def test_update_live_fields_with_late_reason(self, repo):
        event = self._make_event()
        row_id = await repo.insert_event(event)

        live_time = datetime(2026, 4, 15, 15, 12, tzinfo=UTC)
        await repo.update_live_fields(
            row_id, first_seen_live=live_time, transition_delay_sec=720,
            late_reason="COVERAGE_LATE", live_minute=23,
        )

        fetched = await repo.get_by_betpawa_id("99001")
        assert fetched.late_reason == "COVERAGE_LATE"
        assert fetched.live_minute == 23
        assert fetched.transition_delay_sec == 720

    async def test_update_live_fields_without_late_reason(self, repo):
        event = self._make_event()
        row_id = await repo.insert_event(event)

        live_time = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)
        await repo.update_live_fields(
            row_id, first_seen_live=live_time, transition_delay_sec=60,
        )

        fetched = await repo.get_by_betpawa_id("99001")
        assert fetched.late_reason is None
        assert fetched.live_minute is None

    async def test_update_scheduled_kickoff(self, repo):
        event = self._make_event()
        row_id = await repo.insert_event(event)

        new_kickoff = datetime(2026, 4, 15, 16, 0, tzinfo=UTC)
        now = datetime(2026, 4, 15, 15, 6, tzinfo=UTC)
        await repo.update_scheduled_kickoff(
            row_id, new_kickoff=new_kickoff, late_reason="KICKOFF_RESCHEDULED",
            updated_at=now,
        )

        fetched = await repo.get_by_betpawa_id("99001")
        assert fetched.scheduled_kickoff == new_kickoff
        assert fetched.late_reason == "KICKOFF_RESCHEDULED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db.py::TestLateReasonRepository -v`
Expected: FAIL — `update_live_fields` doesn't accept `late_reason`/`live_minute`, `update_scheduled_kickoff` doesn't exist

- [ ] **Step 3: Extend `update_live_fields`**

In `src/live_coverage_bot/db/repository.py`, modify `update_live_fields`:

```python
async def update_live_fields(
    self,
    event_id: int,
    first_seen_live: datetime,
    transition_delay_sec: int,
    late_reason: str | None = None,
    live_minute: int | None = None,
) -> None:
    """Update live-specific fields when event goes live."""
    await self._db.execute(
        """UPDATE events SET first_seen_live = ?, transition_delay_sec = ?,
        late_reason = ?, live_minute = ?, updated_at = ? WHERE id = ?""",
        (
            first_seen_live.isoformat(),
            transition_delay_sec,
            late_reason,
            live_minute,
            first_seen_live.isoformat(),
            event_id,
        ),
    )
```

- [ ] **Step 4: Add `update_scheduled_kickoff` method**

In `src/live_coverage_bot/db/repository.py`, add after `update_live_fields`:

```python
async def update_scheduled_kickoff(
    self,
    event_id: int,
    new_kickoff: datetime,
    late_reason: str,
    updated_at: datetime,
) -> None:
    """Update scheduled kickoff time and late reason (for reschedules)."""
    await self._db.execute(
        """UPDATE events SET scheduled_kickoff = ?, late_reason = ?,
        updated_at = ? WHERE id = ?""",
        (new_kickoff.isoformat(), late_reason, updated_at.isoformat(), event_id),
    )
```

- [ ] **Step 5: Update `_row_to_event` to read new columns**

In `src/live_coverage_bot/db/repository.py`, update `_row_to_event`:

```python
def _row_to_event(self, row: dict) -> TrackedEvent:
    """Convert a database row to a TrackedEvent."""
    return TrackedEvent(
        id=row["id"],
        betpawa_event_id=row["betpawa_event_id"],
        home_team=row["home_team"],
        away_team=row["away_team"],
        competition=row["competition"],
        competition_id=row.get("competition_id", ""),
        country=row["country"],
        scheduled_kickoff=datetime.fromisoformat(row["scheduled_kickoff"]),
        status=EventStatus(row["status"]),
        provider_ids=TrackedEvent.provider_ids_from_json(row["provider_ids"]),
        first_seen_prematch=datetime.fromisoformat(row["first_seen_prematch"]),
        first_seen_live=_parse_dt(row["first_seen_live"]),
        transition_delay_sec=row["transition_delay_sec"],
        slack_message_ts=row["slack_message_ts"],
        removed_at=_parse_dt(row.get("removed_at")),
        pre_removal_market_count=row.get("pre_removal_market_count"),
        late_reason=row.get("late_reason"),
        live_minute=row.get("live_minute"),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )
```

- [ ] **Step 6: Update `insert_event` to include new columns**

In `src/live_coverage_bot/db/repository.py`, update the `insert_event` SQL to include the new columns:

```python
async def insert_event(self, event: TrackedEvent) -> int:
    """Insert a new tracked event. Returns the row ID."""
    cursor = await self._db.execute(
        """INSERT INTO events
        (betpawa_event_id, home_team, away_team, competition, competition_id, country,
         scheduled_kickoff, status, provider_ids, first_seen_prematch,
         first_seen_live, transition_delay_sec, slack_message_ts,
         removed_at, pre_removal_market_count, late_reason, live_minute)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event.betpawa_event_id,
            event.home_team,
            event.away_team,
            event.competition,
            event.competition_id,
            event.country,
            event.scheduled_kickoff.isoformat(),
            event.status.value,
            event.provider_ids_json,
            event.first_seen_prematch.isoformat(),
            event.first_seen_live.isoformat() if event.first_seen_live else None,
            event.transition_delay_sec,
            event.slack_message_ts,
            event.removed_at.isoformat() if event.removed_at else None,
            event.pre_removal_market_count,
            event.late_reason,
            event.live_minute,
        ),
    )
    return cursor.lastrowid
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/test_db.py::TestLateReasonRepository -v`
Expected: PASS

- [ ] **Step 8: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All existing tests still pass (especially `test_update_live_fields` which uses the old signature with keyword args)

- [ ] **Step 9: Commit**

```bash
git add src/live_coverage_bot/db/repository.py tests/test_db.py
git commit -m "feat: extend repository for late_reason and live_minute storage"
```

---

### Task 5: Tracker — Late reason classification in `_evaluate_transition`

**Files:**
- Modify: `src/live_coverage_bot/core/tracker.py:13-24` (constructor)
- Modify: `src/live_coverage_bot/core/tracker.py:57-119` (check_transitions)
- Modify: `src/live_coverage_bot/core/tracker.py:257-329` (_evaluate_transition)
- Test: `tests/test_tracker.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_tracker.py`, add a new test class:

```python
from live_coverage_bot.clients.models import LiveEvent, UpcomingEvent


class TestLateReasonClassification:
    async def test_late_to_live_coverage_late(self, db):
        """Minute > threshold → COVERAGE_LATE."""
        repo = EventRepository(db)
        tracker = EventLifecycleTracker(
            repo, grace_period_minutes=5, hard_timeout_minutes=90,
            coverage_late_minute_threshold=5,
        )
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [{
                "betpawa_event_id": "99001",
                "home_team": "Arsenal", "away_team": "Chelsea",
                "competition": "EPL", "country": "England",
                "scheduled_kickoff": kickoff,
                "provider_ids": _make_provider_ids(),
            }],
            now=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )

        # Go to LATE
        await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=6))

        # Build live events map with minute=23 (coverage late)
        live_event = LiveEvent(
            event_id="bp:99001", home_team="Arsenal", away_team="Chelsea",
            competition_id="1", competition_name="EPL", minute="23",
            home_score=0, away_score=0,
            start_time=kickoff,
        )
        live_map = {"99001": live_event}

        transitions = await tracker.check_transitions(
            {"99001"}, now=kickoff + timedelta(minutes=25),
            live_events_map=live_map,
        )

        assert len(transitions) == 1
        assert transitions[0].new_status == EventStatus.LIVE
        assert transitions[0].live_minute == 23

        event = await repo.get_by_betpawa_id("99001")
        assert event.late_reason == "COVERAGE_LATE"
        assert event.live_minute == 23

    async def test_late_to_live_match_delayed(self, db):
        """Minute <= threshold → MATCH_DELAYED."""
        repo = EventRepository(db)
        tracker = EventLifecycleTracker(
            repo, grace_period_minutes=5, hard_timeout_minutes=90,
            coverage_late_minute_threshold=5,
        )
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [{
                "betpawa_event_id": "99002",
                "home_team": "Liverpool", "away_team": "City",
                "competition": "EPL", "country": "England",
                "scheduled_kickoff": kickoff,
                "provider_ids": _make_provider_ids(),
            }],
            now=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )

        await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=6))

        live_event = LiveEvent(
            event_id="bp:99002", home_team="Liverpool", away_team="City",
            competition_id="1", competition_name="EPL", minute="2",
            home_score=0, away_score=0,
            start_time=kickoff,
        )
        live_map = {"99002": live_event}

        transitions = await tracker.check_transitions(
            {"99002"}, now=kickoff + timedelta(minutes=12),
            live_events_map=live_map,
        )

        assert len(transitions) == 1
        assert transitions[0].live_minute == 2

        event = await repo.get_by_betpawa_id("99002")
        assert event.late_reason == "MATCH_DELAYED"
        assert event.live_minute == 2

    async def test_prematch_to_live_coverage_late(self, db):
        """PREMATCH → LIVE with minute > threshold → COVERAGE_LATE."""
        repo = EventRepository(db)
        tracker = EventLifecycleTracker(
            repo, grace_period_minutes=5, hard_timeout_minutes=90,
            coverage_late_minute_threshold=5,
        )
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [{
                "betpawa_event_id": "99003",
                "home_team": "Spurs", "away_team": "West Ham",
                "competition": "EPL", "country": "England",
                "scheduled_kickoff": kickoff,
                "provider_ids": _make_provider_ids(),
            }],
            now=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )

        live_event = LiveEvent(
            event_id="bp:99003", home_team="Spurs", away_team="West Ham",
            competition_id="1", competition_name="EPL", minute="12",
            home_score=0, away_score=0,
            start_time=kickoff,
        )
        live_map = {"99003": live_event}

        # Goes live within grace period but provider already at minute 12
        transitions = await tracker.check_transitions(
            {"99003"}, now=kickoff + timedelta(minutes=3),
            live_events_map=live_map,
        )

        assert len(transitions) == 1
        assert transitions[0].new_status == EventStatus.LIVE

        event = await repo.get_by_betpawa_id("99003")
        assert event.late_reason == "COVERAGE_LATE"
        assert event.live_minute == 12

    async def test_prematch_to_live_on_time_no_reason(self, db):
        """PREMATCH → LIVE with minute <= threshold → no late_reason."""
        repo = EventRepository(db)
        tracker = EventLifecycleTracker(
            repo, grace_period_minutes=5, hard_timeout_minutes=90,
            coverage_late_minute_threshold=5,
        )
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [{
                "betpawa_event_id": "99004",
                "home_team": "Brighton", "away_team": "Everton",
                "competition": "EPL", "country": "England",
                "scheduled_kickoff": kickoff,
                "provider_ids": _make_provider_ids(),
            }],
            now=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )

        live_event = LiveEvent(
            event_id="bp:99004", home_team="Brighton", away_team="Everton",
            competition_id="1", competition_name="EPL", minute="1",
            home_score=0, away_score=0,
            start_time=kickoff,
        )
        live_map = {"99004": live_event}

        transitions = await tracker.check_transitions(
            {"99004"}, now=kickoff + timedelta(minutes=1),
            live_events_map=live_map,
        )

        assert len(transitions) == 1
        event = await repo.get_by_betpawa_id("99004")
        assert event.late_reason is None
        assert event.live_minute == 1

    async def test_late_to_live_no_map_no_classification(self, db):
        """Without live_events_map, live_minute and late_reason stay None."""
        repo = EventRepository(db)
        tracker = EventLifecycleTracker(
            repo, grace_period_minutes=5, hard_timeout_minutes=90,
            coverage_late_minute_threshold=5,
        )
        kickoff = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [{
                "betpawa_event_id": "99005",
                "home_team": "A", "away_team": "B",
                "competition": "EPL", "country": "England",
                "scheduled_kickoff": kickoff,
                "provider_ids": _make_provider_ids(),
            }],
            now=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )

        await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=6))

        transitions = await tracker.check_transitions(
            {"99005"}, now=kickoff + timedelta(minutes=12),
        )

        assert len(transitions) == 1
        event = await repo.get_by_betpawa_id("99005")
        assert event.late_reason is None
        assert event.live_minute is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracker.py::TestLateReasonClassification -v`
Expected: FAIL — `EventLifecycleTracker` doesn't accept `coverage_late_minute_threshold`, `check_transitions` doesn't accept `live_events_map`

- [ ] **Step 3: Update EventLifecycleTracker constructor**

In `src/live_coverage_bot/core/tracker.py`, update the constructor:

```python
class EventLifecycleTracker:
    """Tracks events through their prematch-to-live lifecycle using SQLite."""

    def __init__(
        self,
        repo: EventRepository,
        grace_period_minutes: int = 5,
        hard_timeout_minutes: int = 90,
        coverage_late_minute_threshold: int = 5,
    ) -> None:
        self._repo = repo
        self._grace_period_minutes = grace_period_minutes
        self._hard_timeout_minutes = hard_timeout_minutes
        self._coverage_late_minute_threshold = coverage_late_minute_threshold
```

- [ ] **Step 4: Update `check_transitions` signature**

In `src/live_coverage_bot/core/tracker.py`, update `check_transitions` to accept and pass through the new maps:

```python
async def check_transitions(
    self,
    live_betpawa_ids: set[str],
    now: datetime,
    *,
    live_events_map: dict[str, Any] | None = None,
    prematch_events_map: dict[str, Any] | None = None,
) -> list[TransitionResult]:
```

Pass the maps down to `_evaluate_transition` in the loop:

```python
transition = await self._evaluate_transition(
    event, is_in_live_feed, now,
    live_events_map=live_events_map,
    prematch_events_map=prematch_events_map,
)
```

- [ ] **Step 5: Update `_evaluate_transition` with classification logic**

In `src/live_coverage_bot/core/tracker.py`, update `_evaluate_transition`:

```python
async def _evaluate_transition(
    self,
    event: TrackedEvent,
    is_in_live_feed: bool,
    now: datetime,
    *,
    live_events_map: dict[str, Any] | None = None,
    prematch_events_map: dict[str, Any] | None = None,
) -> TransitionResult | None:
    """Evaluate a single event for state transition."""
    assert event.id is not None
    elapsed_sec = (now - event.scheduled_kickoff).total_seconds()
    elapsed_min = elapsed_sec / 60
    old_status = event.status

    if event.status == EventStatus.PREMATCH and is_in_live_feed:
        delay_sec = max(0, int(elapsed_sec))
        details = f"Went live ({delay_sec // 60}min after kickoff)" if delay_sec > 0 else "Went live on time"

        # Classify late reason from provider minute
        live_minute, late_reason = self._classify_live_minute(
            event.betpawa_event_id, live_events_map
        )
        # For PREMATCH → LIVE, only set late_reason if coverage was late
        if late_reason == "MATCH_DELAYED":
            late_reason = None  # Normal on-time transition

        await self._repo.update_status(event.id, EventStatus.LIVE, updated_at=now)
        await self._repo.update_live_fields(
            event.id, first_seen_live=now, transition_delay_sec=delay_sec,
            late_reason=late_reason, live_minute=live_minute,
        )
        await self._repo.insert_state_change(
            event.id, old_status, EventStatus.LIVE, now, details=details,
        )
        return TransitionResult(
            event=event,
            old_status=old_status,
            new_status=EventStatus.LIVE,
            delay_sec=delay_sec,
            live_minute=live_minute,
            details=details,
        )

    if event.status == EventStatus.PREMATCH and not is_in_live_feed:
        if elapsed_min >= self._grace_period_minutes:
            details = f"{int(elapsed_min)}min past kickoff"
            await self._repo.update_status(event.id, EventStatus.LATE, updated_at=now)
            await self._repo.insert_state_change(
                event.id, old_status, EventStatus.LATE, now, details=details,
            )
            return TransitionResult(
                event=event,
                old_status=old_status,
                new_status=EventStatus.LATE,
                details=details,
            )

    if event.status == EventStatus.LATE and is_in_live_feed:
        delay_sec = max(0, int(elapsed_sec))

        # Classify late reason from provider minute
        live_minute, late_reason = self._classify_live_minute(
            event.betpawa_event_id, live_events_map
        )

        details = f"Went live (delay: {delay_sec // 60}min)"
        await self._repo.update_status(event.id, EventStatus.LIVE, updated_at=now)
        await self._repo.update_live_fields(
            event.id, first_seen_live=now, transition_delay_sec=delay_sec,
            late_reason=late_reason, live_minute=live_minute,
        )
        await self._repo.insert_state_change(
            event.id, old_status, EventStatus.LIVE, now, details=details,
        )
        return TransitionResult(
            event=event,
            old_status=old_status,
            new_status=EventStatus.LIVE,
            delay_sec=delay_sec,
            live_minute=live_minute,
            details=details,
        )

    if event.status == EventStatus.LATE and not is_in_live_feed:
        if elapsed_min >= self._hard_timeout_minutes:
            details = f"Timed out after {int(elapsed_min)}min"
            await self._repo.update_status(event.id, EventStatus.NEVER_LIVE, updated_at=now)
            await self._repo.insert_state_change(
                event.id, old_status, EventStatus.NEVER_LIVE, now, details=details,
            )
            return TransitionResult(
                event=event,
                old_status=old_status,
                new_status=EventStatus.NEVER_LIVE,
                details=details,
            )

    return None
```

- [ ] **Step 6: Add `_classify_live_minute` helper**

In `src/live_coverage_bot/core/tracker.py`, add a helper method to the class:

```python
def _classify_live_minute(
    self,
    betpawa_event_id: str,
    live_events_map: dict[str, Any] | None,
) -> tuple[int | None, str | None]:
    """Extract live minute and classify late reason from live event data.

    Returns (live_minute, late_reason).
    """
    if live_events_map is None:
        return None, None

    live_event = live_events_map.get(betpawa_event_id)
    if live_event is None:
        return None, None

    minute_str = live_event.minute
    if minute_str is None:
        return None, None

    try:
        minute = int(minute_str)
    except (ValueError, TypeError):
        return None, None

    if minute <= self._coverage_late_minute_threshold:
        return minute, "MATCH_DELAYED"
    else:
        return minute, "COVERAGE_LATE"
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/test_tracker.py::TestLateReasonClassification -v`
Expected: PASS

- [ ] **Step 8: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All existing tests still pass (old calls to `check_transitions` without the new kwargs still work)

- [ ] **Step 9: Commit**

```bash
git add src/live_coverage_bot/core/tracker.py tests/test_tracker.py
git commit -m "feat: classify late reason from provider minute at live transition"
```

---

### Task 6: Tracker — Kickoff reschedule detection

**Files:**
- Modify: `src/live_coverage_bot/core/tracker.py:257-329` (_evaluate_transition, LATE branch)
- Test: `tests/test_tracker.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_tracker.py`, add:

```python
class TestKickoffReschedule:
    async def test_late_reverts_to_prematch_on_reschedule(self, db):
        """LATE event with changed kickoff in prematch → revert to PREMATCH."""
        repo = EventRepository(db)
        tracker = EventLifecycleTracker(
            repo, grace_period_minutes=5, hard_timeout_minutes=90,
            coverage_late_minute_threshold=5,
        )
        kickoff = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [{
                "betpawa_event_id": "99010",
                "home_team": "TeamA", "away_team": "TeamB",
                "competition": "U19 Elit A", "country": "Turkey",
                "scheduled_kickoff": kickoff,
                "provider_ids": _make_provider_ids(),
            }],
            now=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
        )

        # Goes LATE
        await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=6))
        event = await repo.get_by_betpawa_id("99010")
        assert event.status == EventStatus.LATE

        # Prematch feed shows new kickoff 1h later
        new_kickoff = datetime(2026, 4, 15, 13, 0, tzinfo=UTC)
        prematch_event = UpcomingEvent(
            event_id="99010", home_team="TeamA", away_team="TeamB",
            competition_name="U19 Elit A", country_name="Turkey",
            start_time=new_kickoff, provider_ids=_make_provider_ids(),
        )
        prematch_map = {"99010": prematch_event}

        transitions = await tracker.check_transitions(
            set(), now=kickoff + timedelta(minutes=7),
            prematch_events_map=prematch_map,
        )

        assert len(transitions) == 1
        t = transitions[0]
        assert t.old_status == EventStatus.LATE
        assert t.new_status == EventStatus.PREMATCH
        assert "12:00" in t.details
        assert "13:00" in t.details

        event = await repo.get_by_betpawa_id("99010")
        assert event.status == EventStatus.PREMATCH
        assert event.scheduled_kickoff == new_kickoff
        assert event.late_reason == "KICKOFF_RESCHEDULED"

    async def test_late_no_reschedule_if_same_kickoff(self, db):
        """LATE event with same kickoff in prematch → no reschedule, proceed to timeout."""
        repo = EventRepository(db)
        tracker = EventLifecycleTracker(
            repo, grace_period_minutes=5, hard_timeout_minutes=90,
            coverage_late_minute_threshold=5,
        )
        kickoff = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [{
                "betpawa_event_id": "99011",
                "home_team": "TeamC", "away_team": "TeamD",
                "competition": "Test", "country": "Test",
                "scheduled_kickoff": kickoff,
                "provider_ids": _make_provider_ids(),
            }],
            now=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
        )

        await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=6))

        # Same kickoff in prematch map — no reschedule
        prematch_event = UpcomingEvent(
            event_id="99011", home_team="TeamC", away_team="TeamD",
            competition_name="Test", country_name="Test",
            start_time=kickoff, provider_ids=_make_provider_ids(),
        )
        prematch_map = {"99011": prematch_event}

        transitions = await tracker.check_transitions(
            set(), now=kickoff + timedelta(minutes=7),
            prematch_events_map=prematch_map,
        )

        # No transition — event stays LATE
        assert len(transitions) == 0
        event = await repo.get_by_betpawa_id("99011")
        assert event.status == EventStatus.LATE

    async def test_late_no_prematch_map_skips_reschedule(self, db):
        """Without prematch_events_map, reschedule detection is skipped."""
        repo = EventRepository(db)
        tracker = EventLifecycleTracker(
            repo, grace_period_minutes=5, hard_timeout_minutes=90,
            coverage_late_minute_threshold=5,
        )
        kickoff = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [{
                "betpawa_event_id": "99012",
                "home_team": "TeamE", "away_team": "TeamF",
                "competition": "Test", "country": "Test",
                "scheduled_kickoff": kickoff,
                "provider_ids": _make_provider_ids(),
            }],
            now=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
        )

        await tracker.check_transitions(set(), now=kickoff + timedelta(minutes=6))

        # No prematch map — just stays LATE
        transitions = await tracker.check_transitions(
            set(), now=kickoff + timedelta(minutes=7),
        )
        assert len(transitions) == 0

    async def test_reschedule_then_normal_live(self, db):
        """Full lifecycle: PREMATCH → LATE → PREMATCH (reschedule) → LIVE."""
        repo = EventRepository(db)
        tracker = EventLifecycleTracker(
            repo, grace_period_minutes=5, hard_timeout_minutes=90,
            coverage_late_minute_threshold=5,
        )
        old_kickoff = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        new_kickoff = datetime(2026, 4, 15, 13, 0, tzinfo=UTC)

        await tracker.register_prematch_events(
            [{
                "betpawa_event_id": "99013",
                "home_team": "TeamG", "away_team": "TeamH",
                "competition": "Test", "country": "Test",
                "scheduled_kickoff": old_kickoff,
                "provider_ids": _make_provider_ids(),
            }],
            now=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
        )

        # PREMATCH → LATE
        await tracker.check_transitions(set(), now=old_kickoff + timedelta(minutes=6))

        # LATE → PREMATCH (reschedule)
        prematch_event = UpcomingEvent(
            event_id="99013", home_team="TeamG", away_team="TeamH",
            competition_name="Test", country_name="Test",
            start_time=new_kickoff, provider_ids=_make_provider_ids(),
        )
        await tracker.check_transitions(
            set(), now=old_kickoff + timedelta(minutes=7),
            prematch_events_map={"99013": prematch_event},
        )

        # PREMATCH → LIVE at new kickoff
        live_event = LiveEvent(
            event_id="bp:99013", home_team="TeamG", away_team="TeamH",
            competition_id="1", competition_name="Test", minute="1",
            home_score=0, away_score=0,
            start_time=new_kickoff,
        )
        transitions = await tracker.check_transitions(
            {"99013"}, now=new_kickoff + timedelta(minutes=1),
            live_events_map={"99013": live_event},
        )

        assert len(transitions) == 1
        assert transitions[0].new_status == EventStatus.LIVE

        event = await repo.get_by_betpawa_id("99013")
        assert event.status == EventStatus.LIVE
        # Delay from NEW kickoff should be ~60 sec
        assert event.transition_delay_sec == 60
        # late_reason gets overwritten to None (normal on-time transition)
        assert event.live_minute == 1

        # Verify state change history
        changes = await repo.get_state_changes(event.id)
        statuses = [(c.old_status, c.new_status) for c in changes]
        assert (EventStatus.PREMATCH, EventStatus.LATE) in statuses
        assert (EventStatus.LATE, EventStatus.PREMATCH) in statuses
        assert (EventStatus.PREMATCH, EventStatus.LIVE) in statuses
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracker.py::TestKickoffReschedule -v`
Expected: FAIL — reschedule logic not implemented yet

- [ ] **Step 3: Add reschedule detection to `_evaluate_transition`**

In `src/live_coverage_bot/core/tracker.py`, update the `LATE and not is_in_live_feed` branch to check for reschedule **before** the hard timeout check:

```python
    if event.status == EventStatus.LATE and not is_in_live_feed:
        # Check for kickoff reschedule before hard timeout
        if prematch_events_map is not None:
            prematch_event = prematch_events_map.get(event.betpawa_event_id)
            if prematch_event is not None:
                time_diff = abs((prematch_event.start_time - event.scheduled_kickoff).total_seconds())
                if time_diff > 60:  # More than 1 minute difference
                    old_kickoff_str = event.scheduled_kickoff.strftime("%H:%M")
                    new_kickoff_str = prematch_event.start_time.strftime("%H:%M")
                    details = f"Kickoff rescheduled: {old_kickoff_str} \u2192 {new_kickoff_str} UTC"
                    await self._repo.update_scheduled_kickoff(
                        event.id, new_kickoff=prematch_event.start_time,
                        late_reason="KICKOFF_RESCHEDULED", updated_at=now,
                    )
                    await self._repo.update_status(event.id, EventStatus.PREMATCH, updated_at=now)
                    await self._repo.insert_state_change(
                        event.id, old_status, EventStatus.PREMATCH, now,
                        details=details,
                    )
                    return TransitionResult(
                        event=event,
                        old_status=old_status,
                        new_status=EventStatus.PREMATCH,
                        details=details,
                    )

        if elapsed_min >= self._hard_timeout_minutes:
            details = f"Timed out after {int(elapsed_min)}min"
            await self._repo.update_status(event.id, EventStatus.NEVER_LIVE, updated_at=now)
            await self._repo.insert_state_change(
                event.id, old_status, EventStatus.NEVER_LIVE, now, details=details,
            )
            return TransitionResult(
                event=event,
                old_status=old_status,
                new_status=EventStatus.NEVER_LIVE,
                details=details,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracker.py::TestKickoffReschedule -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/core/tracker.py tests/test_tracker.py
git commit -m "feat: detect kickoff reschedules and revert LATE to PREMATCH"
```

---

### Task 7: Slack — Add late reason and reschedule formatting

**Files:**
- Modify: `src/live_coverage_bot/clients/slack.py:64-96` (format_parent_message)
- Modify: `src/live_coverage_bot/clients/slack.py:130-148` (format_thread_reply)
- Test: `tests/test_slack.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_slack.py`, add:

```python
class TestLateReasonFormatting:
    def test_went_live_coverage_late(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.LIVE
        sample_event.first_seen_live = datetime(2026, 4, 15, 15, 25, tzinfo=UTC)
        sample_event.transition_delay_sec = 1500
        sample_event.late_reason = "COVERAGE_LATE"
        sample_event.live_minute = 23
        text = client.format_parent_message(sample_event)
        assert "WENT LIVE" in text
        assert "23'" in text
        assert "coverage started late" in text.lower()

    def test_went_live_match_delayed(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.LIVE
        sample_event.first_seen_live = datetime(2026, 4, 15, 15, 12, tzinfo=UTC)
        sample_event.transition_delay_sec = 720
        sample_event.late_reason = "MATCH_DELAYED"
        sample_event.live_minute = 2
        text = client.format_parent_message(sample_event)
        assert "WENT LIVE" in text
        assert "2'" in text
        assert "started late" in text.lower()

    def test_went_live_no_late_reason(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.LIVE
        sample_event.first_seen_live = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)
        sample_event.transition_delay_sec = 60
        sample_event.late_reason = None
        sample_event.live_minute = 1
        text = client.format_parent_message(sample_event)
        assert "WENT LIVE" in text
        assert "coverage started late" not in text.lower()

    def test_format_reschedule_message(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        old_kickoff = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        new_kickoff = datetime(2026, 4, 15, 13, 0, tzinfo=UTC)
        sample_event.scheduled_kickoff = new_kickoff
        text = client.format_reschedule_message(sample_event, old_kickoff, new_kickoff)
        assert "RESCHEDULED" in text
        assert "12:00" in text
        assert "13:00" in text
        assert "Arsenal vs Chelsea" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_slack.py::TestLateReasonFormatting -v`
Expected: FAIL — `late_reason`/`live_minute` not shown in formatting, `format_reschedule_message` doesn't exist

- [ ] **Step 3: Update `format_parent_message` for LIVE status**

In `src/live_coverage_bot/clients/slack.py`, update the LIVE block in `format_parent_message`:

```python
if event.status == EventStatus.LIVE:
    live_str = ""
    delay_str = ""
    if event.first_seen_live:
        live_str = event.first_seen_live.strftime("%H:%M UTC")
    if event.transition_delay_sec is not None:
        delay_min = event.transition_delay_sec // 60
        delay_str = f"+{delay_min}min"

    lines = [
        f"{EMOJI_LIVE} WENT LIVE {EMOJI_DASH} {event.home_team} vs {event.away_team}",
        f"{EMOJI_CLIPBOARD} {competition_line}",
        f"{EMOJI_CLOCK} Kickoff: {kickoff_str} | Live at: {live_str} ({delay_str})",
        f"{EMOJI_PLUG} {provider_str}",
        f"{EMOJI_ID} BetPawa ID: {event.betpawa_event_id}",
    ]

    if event.late_reason == "COVERAGE_LATE" and event.live_minute is not None:
        lines.append(
            f"{EMOJI_WARNING} Provider at minute {event.live_minute}' \u2014 coverage started late"
        )
    elif event.late_reason == "MATCH_DELAYED" and event.live_minute is not None:
        lines.append(f"Match started late (minute {event.live_minute}')")

    return "\n".join(lines)
```

- [ ] **Step 4: Add `format_reschedule_message` method**

In `src/live_coverage_bot/clients/slack.py`, add a new method:

```python
EMOJI_CALENDAR = "\U0001f4c5"  # 📅

def format_reschedule_message(
    self, event: TrackedEvent, old_kickoff: datetime, new_kickoff: datetime,
) -> str:
    """Format a rescheduled event parent message."""
    provider_str, competition_line, _ = self._format_event_header(event)
    old_str = old_kickoff.strftime("%H:%M UTC")
    new_str = new_kickoff.strftime("%H:%M UTC")
    return (
        f"{EMOJI_CALENDAR} RESCHEDULED {EMOJI_DASH} {event.home_team} vs {event.away_team}\n"
        f"{EMOJI_CLIPBOARD} {competition_line}\n"
        f"{EMOJI_CLOCK} Kickoff: {old_str} \u2192 {new_str}\n"
        f"{EMOJI_PLUG} {provider_str}\n"
        f"{EMOJI_ID} BetPawa ID: {event.betpawa_event_id}"
    )
```

Add the `EMOJI_CALENDAR` constant near the top of the file with the other emoji constants.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_slack.py::TestLateReasonFormatting -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/live_coverage_bot/clients/slack.py tests/test_slack.py
git commit -m "feat: add late reason and reschedule formatting to Slack alerts"
```

---

### Task 8: Loop — Build maps and handle new transitions

**Files:**
- Modify: `src/live_coverage_bot/core/loop.py:45-49` (tracker construction)
- Modify: `src/live_coverage_bot/core/loop.py:149-207` (_poll_cycle)
- Modify: `src/live_coverage_bot/core/loop.py:273-353` (_handle_transition)
- Test: `tests/test_loop.py`

- [ ] **Step 1: Read existing test_loop.py to understand test patterns**

Run: Read `tests/test_loop.py` to see existing test patterns and mocking approach used.

- [ ] **Step 2: Update tracker construction in `run()` method**

In `src/live_coverage_bot/core/loop.py`, update the tracker construction (around line 45):

```python
self._tracker = EventLifecycleTracker(
    self._repo,
    grace_period_minutes=self._settings.thresholds.grace_period_minutes,
    hard_timeout_minutes=self._settings.thresholds.hard_timeout_minutes,
    coverage_late_minute_threshold=self._settings.thresholds.coverage_late_minute_threshold,
)
```

- [ ] **Step 3: Build maps and pass to `check_transitions`**

In `src/live_coverage_bot/core/loop.py`, update `_poll_cycle` to build the maps:

After `live_betpawa_ids = BetPawaClient.build_live_betpawa_id_set(live_events)`, add:

```python
# Build betpawa_id → LiveEvent map for late reason classification
live_events_map: dict[str, Any] = {}
for le in live_events:
    eid = le.event_id
    if eid.startswith("bp:"):
        eid = eid[3:]
    live_events_map[eid] = le
```

After the prematch feed is processed, build the prematch map:

```python
prematch_events_map: dict[str, Any] | None = None
```

Set it when prematch is fetched (inside the `if should_fetch_prematch:` block, after `current_prematch_ids` is set):

```python
prematch_events_map = {e.event_id: e for e in upcoming}
```

Update the `check_transitions` call:

```python
transitions = await self._tracker.check_transitions(
    live_betpawa_ids, now=now,
    live_events_map=live_events_map,
    prematch_events_map=prematch_events_map,
)
```

- [ ] **Step 4: Handle `LATE → PREMATCH` transition in `_handle_transition`**

In `src/live_coverage_bot/core/loop.py`, update `_handle_transition` to handle reschedule. Add a new branch before the `elif new_status in (EventStatus.LIVE, EventStatus.NEVER_LIVE):` block:

```python
elif old_status == EventStatus.LATE and new_status == EventStatus.PREMATCH:
    # Kickoff rescheduled — update parent message and add thread reply
    if refreshed.slack_message_ts:
        # Parse old/new kickoff from details string
        # Details format: "Kickoff rescheduled: HH:MM → HH:MM UTC"
        old_kickoff_str = transition.details.split(": ")[1].split(" \u2192 ")[0]
        new_kickoff_str = transition.details.split("\u2192 ")[1].replace(" UTC", "")
        # Build datetimes for formatting (use event date)
        event_date = refreshed.scheduled_kickoff.date()
        from datetime import time as dt_time
        old_h, old_m = map(int, old_kickoff_str.split(":"))
        new_h, new_m = map(int, new_kickoff_str.split(":"))
        old_kickoff = datetime.combine(
            event_date, dt_time(old_h, old_m), tzinfo=UTC
        )
        new_kickoff = datetime.combine(
            event_date, dt_time(new_h, new_m), tzinfo=UTC
        )

        reschedule_text = slack.format_reschedule_message(
            refreshed, old_kickoff, new_kickoff
        )
        await slack.update_message(refreshed.slack_message_ts, reschedule_text)

        reply = (
            f"{EMOJI_CALENDAR} Kickoff rescheduled: "
            f"{old_kickoff_str} \u2192 {new_kickoff_str} UTC "
            f"\u2014 reverted to prematch monitoring"
        )
        await slack.post_thread_reply(refreshed.slack_message_ts, reply)
        logger.info(
            "Reschedule: %s %s vs %s (%s -> %s UTC)",
            refreshed.betpawa_event_id, refreshed.home_team,
            refreshed.away_team, old_kickoff_str, new_kickoff_str,
        )
```

Add the calendar emoji import at the top of the method or use the constant from slack module:

```python
from live_coverage_bot.clients.slack import EMOJI_CALENDAR
```

- [ ] **Step 5: Update thread reply for LATE → LIVE with late reason**

In `_handle_transition`, update the `elif new_status in (EventStatus.LIVE, EventStatus.NEVER_LIVE):` block to include live_minute in the detail:

```python
elif new_status in (EventStatus.LIVE, EventStatus.NEVER_LIVE):
    if refreshed.slack_message_ts:
        await slack.update_message(refreshed.slack_message_ts, refreshed, now)
        delay_sec = transition.delay_sec
        if new_status == EventStatus.LIVE and delay_sec is not None:
            detail = f"delay: {delay_sec // 60}min"
            if transition.live_minute is not None:
                detail += f", provider minute: {transition.live_minute}'"
        elif new_status == EventStatus.NEVER_LIVE:
            detail = "timed out"
        else:
            detail = None
        reply_text = slack.format_thread_reply(
            old_status, new_status, now, detail
        )
        await slack.post_thread_reply(refreshed.slack_message_ts, reply_text)
```

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/live_coverage_bot/core/loop.py
git commit -m "feat: build live/prematch maps and handle reschedule transitions in loop"
```

---

### Task 9: Weekly Report — Late reason breakdown

**Files:**
- Modify: `src/live_coverage_bot/core/reporter.py:62-138` (generate_slack_summary)
- Modify: `src/live_coverage_bot/core/reporter.py:140-173` (generate_csv)
- Test: `tests/test_reporter.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_reporter.py`, add:

```python
class TestLateReasonReport:
    async def test_summary_includes_late_reason_breakdown(self, reporter, repo):
        base = datetime(2026, 4, 14, tzinfo=UTC)
        pids = [ProviderID(type=ProviderType.SPORTRADAR, id="100")]
        events = [
            TrackedEvent(
                betpawa_event_id="10", home_team="A", away_team="B",
                competition="EPL", country="England",
                scheduled_kickoff=base, status=EventStatus.LIVE,
                provider_ids=pids, first_seen_prematch=base,
                first_seen_live=base, transition_delay_sec=600,
                late_reason="COVERAGE_LATE", live_minute=12,
            ),
            TrackedEvent(
                betpawa_event_id="11", home_team="C", away_team="D",
                competition="EPL", country="England",
                scheduled_kickoff=base, status=EventStatus.LIVE,
                provider_ids=pids, first_seen_prematch=base,
                first_seen_live=base, transition_delay_sec=720,
                late_reason="MATCH_DELAYED", live_minute=3,
            ),
            TrackedEvent(
                betpawa_event_id="12", home_team="E", away_team="F",
                competition="EPL", country="England",
                scheduled_kickoff=base, status=EventStatus.LIVE,
                provider_ids=pids, first_seen_prematch=base,
                first_seen_live=base, transition_delay_sec=0,
                late_reason="KICKOFF_RESCHEDULED",
            ),
        ]
        for e in events:
            await repo.insert_event(e)

        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        assert "Coverage late" in summary or "coverage late" in summary
        assert "Match delayed" in summary or "match delayed" in summary
        assert "Rescheduled" in summary or "rescheduled" in summary

    async def test_csv_includes_late_reason_columns(self, reporter, repo):
        base = datetime(2026, 4, 14, tzinfo=UTC)
        pids = [ProviderID(type=ProviderType.SPORTRADAR, id="100")]
        event = TrackedEvent(
            betpawa_event_id="20", home_team="G", away_team="H",
            competition="EPL", country="England",
            scheduled_kickoff=base, status=EventStatus.LIVE,
            provider_ids=pids, first_seen_prematch=base,
            first_seen_live=base, transition_delay_sec=600,
            late_reason="COVERAGE_LATE", live_minute=15,
        )
        await repo.insert_event(event)

        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        csv_content = await reporter.generate_csv(start, end)
        reader = csv.DictReader(io.StringIO(csv_content))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["late_reason"] == "COVERAGE_LATE"
        assert rows[0]["live_minute"] == "15"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reporter.py::TestLateReasonReport -v`
Expected: FAIL — summary doesn't include late reason breakdown, CSV lacks columns

- [ ] **Step 3: Add late reason breakdown to `generate_slack_summary`**

In `src/live_coverage_bot/core/reporter.py`, add after the `if late:` block in `generate_slack_summary` (around line 108):

```python
# Late reason breakdown
reasons = [e.late_reason for e in events if e.late_reason]
if reasons:
    from collections import Counter as ReasonCounter
    reason_counts = ReasonCounter(reasons)
    lines.append("")
    lines.append("Late reason breakdown:")
    label_map = {
        "COVERAGE_LATE": "Coverage late",
        "MATCH_DELAYED": "Match delayed",
        "KICKOFF_RESCHEDULED": "Rescheduled",
    }
    for reason, count in reason_counts.most_common():
        label = label_map.get(reason, reason)
        lines.append(f"  {label}: {count}")
```

- [ ] **Step 4: Add `late_reason` and `live_minute` to CSV**

In `src/live_coverage_bot/core/reporter.py`, update `generate_csv`:

```python
writer = csv.DictWriter(
    output,
    fieldnames=[
        "date", "betpawa_event_id", "home_team", "away_team",
        "competition", "country", "provider_type", "provider_id",
        "scheduled_kickoff", "status", "first_seen_live",
        "transition_delay_sec", "late_reason", "live_minute",
    ],
)
```

And add the new fields to the `writerow` call:

```python
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
    "late_reason": event.late_reason or "",
    "live_minute": event.live_minute if event.live_minute is not None else "",
})
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_reporter.py::TestLateReasonReport -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass (check that `test_generate_csv_content` still passes — the field count check should still work since it checks by key, not count)

- [ ] **Step 7: Commit**

```bash
git add src/live_coverage_bot/core/reporter.py tests/test_reporter.py
git commit -m "feat: add late reason breakdown to weekly report and CSV"
```

---

### Task 10: Final integration test and lint

**Files:**
- All modified files

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 2: Run linter**

Run: `python -m ruff check src/ tests/`
Expected: No errors (or only pre-existing ones)

- [ ] **Step 3: Run formatter check**

Run: `python -m ruff format --check src/ tests/`
Expected: No formatting issues

- [ ] **Step 4: Fix any issues found**

If linter or formatter reports issues, fix them.

- [ ] **Step 5: Final commit if any fixes**

```bash
git add -A
git commit -m "chore: lint and format fixes"
```
