# Downtime Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Slack spam on restart by detecting downtime, marking stale events as UNMONITORED, and notifying the channel about bot lifecycle events (shutdown, crash, recovery).

**Architecture:** A `bot_state` table stores a heartbeat timestamp updated every poll cycle. On startup, the bot checks the gap since last heartbeat. If significant, it bulk-transitions stale events to a new `UNMONITORED` terminal status and posts a single recovery summary instead of individual alerts. Graceful shutdown and crash handlers post status messages to Slack before exiting.

**Tech Stack:** Python 3.11+, aiosqlite, httpx (Slack API), pytest + pytest-asyncio

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/live_coverage_bot/models/events.py` | Modify | Add `UNMONITORED` to `EventStatus` enum |
| `src/live_coverage_bot/db/connection.py` | Modify | Add `bot_state` table to schema |
| `src/live_coverage_bot/db/repository.py` | Modify | Add heartbeat read/write + `get_active_events_with_past_kickoff` methods |
| `src/live_coverage_bot/core/tracker.py` | Modify | Add `mark_unmonitored()` method |
| `src/live_coverage_bot/clients/slack.py` | Modify | Add `post_bot_status()` and `format_recovery_summary()` methods |
| `src/live_coverage_bot/core/loop.py` | Modify | Add heartbeat writes, startup recovery, crash notification |
| `src/live_coverage_bot/__main__.py` | Modify | Add graceful shutdown Slack notification |
| `src/live_coverage_bot/core/reporter.py` | Modify | Handle `UNMONITORED` in weekly summary and stats |
| `tests/test_models.py` | Modify | Test `UNMONITORED` status properties |
| `tests/test_db.py` | Modify | Test heartbeat and bot_state table operations |
| `tests/test_tracker.py` | Modify | Test `mark_unmonitored()` method |
| `tests/test_reporter.py` | Modify | Test weekly report with `UNMONITORED` events |
| `tests/test_slack.py` | Modify | Test bot status formatting |

---

### Task 1: Add UNMONITORED Status to EventStatus Enum

**Files:**
- Modify: `src/live_coverage_bot/models/events.py:13-25`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write test for UNMONITORED status**

Add to `tests/test_models.py`:

```python
from live_coverage_bot.models.events import EventStatus


class TestEventStatus:
    def test_unmonitored_is_terminal(self):
        assert EventStatus.UNMONITORED.is_terminal is True

    def test_unmonitored_value(self):
        assert EventStatus.UNMONITORED.value == "UNMONITORED"

    def test_existing_terminal_states_unchanged(self):
        assert EventStatus.LIVE.is_terminal is True
        assert EventStatus.NEVER_LIVE.is_terminal is True

    def test_non_terminal_states_unchanged(self):
        assert EventStatus.PREMATCH.is_terminal is False
        assert EventStatus.LATE.is_terminal is False
        assert EventStatus.REMOVED.is_terminal is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py::TestEventStatus -v`
Expected: FAIL — `EventStatus` has no member `UNMONITORED`

- [ ] **Step 3: Add UNMONITORED to the enum**

In `src/live_coverage_bot/models/events.py`, add `UNMONITORED` to the `EventStatus` enum and update `is_terminal`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py::TestEventStatus -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `pytest tests/ -v`
Expected: All existing tests pass

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/models/events.py tests/test_models.py
git commit -m "feat: add UNMONITORED terminal status to EventStatus enum"
```

---

### Task 2: Add bot_state Table to Database Schema

**Files:**
- Modify: `src/live_coverage_bot/db/connection.py:10-73`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write test for bot_state table existence**

Add to `tests/test_db.py`:

```python
class TestBotStateSchema:
    async def test_bot_state_table_exists(self, db: Database):
        tables = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = [row["name"] for row in tables]
        assert "bot_state" in table_names

    async def test_bot_state_columns(self, db: Database):
        columns = await db.fetch_all("PRAGMA table_info(bot_state)")
        col_names = {row["name"] for row in columns}
        assert "id" in col_names
        assert "last_heartbeat" in col_names
        assert "started_at" in col_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py::TestBotStateSchema -v`
Expected: FAIL — `bot_state` table does not exist

- [ ] **Step 3: Add bot_state table to SCHEMA_SQL**

In `src/live_coverage_bot/db/connection.py`, append to the `SCHEMA_SQL` string (before the `CREATE INDEX` statements):

```sql
CREATE TABLE IF NOT EXISTS bot_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_heartbeat TEXT NOT NULL,
    started_at TEXT NOT NULL
);
```

The `CHECK (id = 1)` constraint ensures only a single row can exist.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py::TestBotStateSchema -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/db/connection.py tests/test_db.py
git commit -m "feat: add bot_state table for heartbeat tracking"
```

---

### Task 3: Add Heartbeat Read/Write Methods to EventRepository

**Files:**
- Modify: `src/live_coverage_bot/db/repository.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write tests for heartbeat operations**

Add to `tests/test_db.py`:

```python
from datetime import UTC, datetime, timedelta


class TestHeartbeatOperations:
    @pytest.fixture
    def repo(self, db: Database) -> EventRepository:
        return EventRepository(db)

    async def test_get_last_heartbeat_returns_none_on_first_run(self, repo):
        result = await repo.get_last_heartbeat()
        assert result is None

    async def test_upsert_heartbeat_creates_row(self, repo):
        now = datetime(2026, 4, 20, 10, 0, tzinfo=UTC)
        await repo.upsert_heartbeat(now, started_at=now)
        result = await repo.get_last_heartbeat()
        assert result == now

    async def test_upsert_heartbeat_updates_existing(self, repo):
        start = datetime(2026, 4, 20, 10, 0, tzinfo=UTC)
        await repo.upsert_heartbeat(start, started_at=start)

        later = datetime(2026, 4, 20, 10, 1, tzinfo=UTC)
        await repo.upsert_heartbeat(later)

        result = await repo.get_last_heartbeat()
        assert result == later

    async def test_upsert_heartbeat_preserves_started_at(self, repo):
        start = datetime(2026, 4, 20, 10, 0, tzinfo=UTC)
        await repo.upsert_heartbeat(start, started_at=start)

        later = datetime(2026, 4, 20, 10, 1, tzinfo=UTC)
        await repo.upsert_heartbeat(later)

        row = await repo._db.fetch_one("SELECT started_at FROM bot_state WHERE id = 1")
        assert row is not None
        assert datetime.fromisoformat(row["started_at"]) == start
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py::TestHeartbeatOperations -v`
Expected: FAIL — `EventRepository` has no method `get_last_heartbeat`

- [ ] **Step 3: Implement heartbeat methods**

Add to `src/live_coverage_bot/db/repository.py` in the `EventRepository` class:

```python
async def get_last_heartbeat(self) -> datetime | None:
    """Read the last heartbeat timestamp. Returns None on first run."""
    row = await self._db.fetch_one(
        "SELECT last_heartbeat FROM bot_state WHERE id = 1"
    )
    if row is None:
        return None
    return datetime.fromisoformat(row["last_heartbeat"])

async def upsert_heartbeat(
    self, now: datetime, started_at: datetime | None = None
) -> None:
    """Insert or update the heartbeat timestamp.

    On first call after startup, pass started_at to record session start.
    Subsequent calls within the same session only update last_heartbeat.
    """
    if started_at is not None:
        await self._db.execute(
            """INSERT INTO bot_state (id, last_heartbeat, started_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_heartbeat = excluded.last_heartbeat,
                started_at = excluded.started_at""",
            (now.isoformat(), started_at.isoformat()),
        )
    else:
        await self._db.execute(
            "UPDATE bot_state SET last_heartbeat = ? WHERE id = 1",
            (now.isoformat(),),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py::TestHeartbeatOperations -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/db/repository.py tests/test_db.py
git commit -m "feat: add heartbeat read/write methods to EventRepository"
```

---

### Task 4: Add mark_unmonitored Method to Tracker

**Files:**
- Modify: `src/live_coverage_bot/core/tracker.py`
- Modify: `tests/test_tracker.py`

- [ ] **Step 1: Write tests for mark_unmonitored**

Add to `tests/test_tracker.py`:

```python
class TestMarkUnmonitored:
    async def test_marks_past_kickoff_events_as_unmonitored(self, tracker, repo):
        kickoff_past = datetime(2026, 4, 18, 15, 0, tzinfo=UTC)
        now_register = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99001",
                    "home_team": "Arsenal",
                    "away_team": "Chelsea",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff_past,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_register,
        )

        downtime_start = datetime(2026, 4, 18, 14, 0, tzinfo=UTC)
        now_restart = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)

        count = await tracker.mark_unmonitored(
            downtime_start=downtime_start, now=now_restart
        )
        assert count == 1

        event = await repo.get_by_betpawa_id("99001")
        assert event.status == EventStatus.UNMONITORED

    async def test_leaves_future_kickoff_events_alone(self, tracker, repo):
        kickoff_future = datetime(2026, 4, 21, 15, 0, tzinfo=UTC)
        now_register = datetime(2026, 4, 20, 8, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99002",
                    "home_team": "Liverpool",
                    "away_team": "Man City",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff_future,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_register,
        )

        downtime_start = datetime(2026, 4, 20, 8, 30, tzinfo=UTC)
        now_restart = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)

        count = await tracker.mark_unmonitored(
            downtime_start=downtime_start, now=now_restart
        )
        assert count == 0

        event = await repo.get_by_betpawa_id("99002")
        assert event.status == EventStatus.PREMATCH

    async def test_marks_late_events_as_unmonitored(self, tracker, repo):
        kickoff = datetime(2026, 4, 18, 15, 0, tzinfo=UTC)
        now_register = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99003",
                    "home_team": "Spurs",
                    "away_team": "West Ham",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_register,
        )
        # Transition to LATE before shutdown
        now_late = kickoff + timedelta(minutes=6)
        await tracker.check_transitions(set(), now=now_late)

        downtime_start = datetime(2026, 4, 18, 15, 10, tzinfo=UTC)
        now_restart = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)

        count = await tracker.mark_unmonitored(
            downtime_start=downtime_start, now=now_restart
        )
        assert count == 1

        event = await repo.get_by_betpawa_id("99003")
        assert event.status == EventStatus.UNMONITORED

    async def test_records_state_change_for_unmonitored(self, tracker, repo):
        kickoff = datetime(2026, 4, 18, 15, 0, tzinfo=UTC)
        now_register = datetime(2026, 4, 18, 12, 0, tzinfo=UTC)
        await tracker.register_prematch_events(
            [
                {
                    "betpawa_event_id": "99004",
                    "home_team": "Brighton",
                    "away_team": "Everton",
                    "competition": "EPL",
                    "country": "England",
                    "scheduled_kickoff": kickoff,
                    "provider_ids": _make_provider_ids(),
                }
            ],
            now=now_register,
        )

        downtime_start = datetime(2026, 4, 18, 14, 0, tzinfo=UTC)
        now_restart = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)

        await tracker.mark_unmonitored(
            downtime_start=downtime_start, now=now_restart
        )

        event = await repo.get_by_betpawa_id("99004")
        changes = await repo.get_state_changes(event.id)
        unmonitored_change = [c for c in changes if c.new_status == EventStatus.UNMONITORED]
        assert len(unmonitored_change) == 1
        assert "offline" in unmonitored_change[0].details.lower()

    async def test_returns_zero_when_no_stale_events(self, tracker, repo):
        downtime_start = datetime(2026, 4, 20, 8, 0, tzinfo=UTC)
        now_restart = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)

        count = await tracker.mark_unmonitored(
            downtime_start=downtime_start, now=now_restart
        )
        assert count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tracker.py::TestMarkUnmonitored -v`
Expected: FAIL — `EventLifecycleTracker` has no method `mark_unmonitored`

- [ ] **Step 3: Implement mark_unmonitored**

Add to `src/live_coverage_bot/core/tracker.py` in the `EventLifecycleTracker` class:

```python
async def mark_unmonitored(
    self,
    downtime_start: datetime,
    now: datetime,
) -> int:
    """Mark active events with past kickoff as UNMONITORED after a downtime gap.

    Any active event (PREMATCH or LATE) whose scheduled_kickoff is before
    `now` gets transitioned to UNMONITORED. Events with future kickoffs
    are left alone for normal monitoring.

    Returns the count of events marked.
    """
    active_events = await self._repo.get_active_events()
    count = 0

    for event in active_events:
        assert event.id is not None
        if event.scheduled_kickoff >= now:
            continue

        old_status = event.status
        details = (
            f"Bot was offline from {downtime_start.strftime('%a %b %d %H:%M')} "
            f"to {now.strftime('%a %b %d %H:%M')} UTC"
        )
        await self._repo.update_status(event.id, EventStatus.UNMONITORED, updated_at=now)
        await self._repo.insert_state_change(
            event.id, old_status, EventStatus.UNMONITORED, now,
            details=details,
        )
        logger.info(
            "Marked as UNMONITORED: %s %s vs %s (kickoff %s)",
            event.betpawa_event_id, event.home_team, event.away_team,
            event.scheduled_kickoff.strftime("%Y-%m-%d %H:%M"),
        )
        count += 1

    return count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tracker.py::TestMarkUnmonitored -v`
Expected: PASS

- [ ] **Step 5: Run full tracker tests for regressions**

Run: `pytest tests/test_tracker.py -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/core/tracker.py tests/test_tracker.py
git commit -m "feat: add mark_unmonitored method for downtime recovery"
```

---

### Task 5: Add Bot Status Methods to SlackClient

**Files:**
- Modify: `src/live_coverage_bot/clients/slack.py`
- Modify: `tests/test_slack.py`

- [ ] **Step 1: Read existing Slack tests to follow patterns**

Run: `cat tests/test_slack.py` to understand the testing approach used.

- [ ] **Step 2: Write tests for bot status formatting and posting**

Add to `tests/test_slack.py`:

```python
from datetime import UTC, datetime


class TestBotStatusFormatting:
    def test_format_shutdown_message(self):
        from live_coverage_bot.clients.slack import SlackClient
        from live_coverage_bot.config.models import SlackConfig

        config = SlackConfig(bot_token="xoxb-test", channel_id="C123")
        client = SlackClient(config)

        now = datetime(2026, 4, 20, 14, 32, tzinfo=UTC)
        msg = client.format_shutdown_message(now)

        assert "shutting down" in msg.lower()
        assert "14:32" in msg

    def test_format_crash_message(self):
        from live_coverage_bot.clients.slack import SlackClient
        from live_coverage_bot.config.models import SlackConfig

        config = SlackConfig(bot_token="xoxb-test", channel_id="C123")
        client = SlackClient(config)

        msg = client.format_crash_message("ValueError", "invalid literal")

        assert "crashed" in msg.lower()
        assert "ValueError" in msg
        assert "invalid literal" in msg

    def test_format_recovery_summary(self):
        from live_coverage_bot.clients.slack import SlackClient
        from live_coverage_bot.config.models import SlackConfig

        config = SlackConfig(bot_token="xoxb-test", channel_id="C123")
        client = SlackClient(config)

        downtime_start = datetime(2026, 4, 18, 14, 32, tzinfo=UTC)
        now = datetime(2026, 4, 20, 9, 15, tzinfo=UTC)

        msg = client.format_recovery_summary(
            downtime_start=downtime_start,
            now=now,
            unmonitored_count=12,
            active_remaining=3,
        )

        assert "restarting" in msg.lower() or "downtime" in msg.lower()
        assert "12" in msg
        assert "3" in msg
        assert "Apr 18" in msg or "18" in msg
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_slack.py::TestBotStatusFormatting -v`
Expected: FAIL — methods don't exist

- [ ] **Step 4: Implement bot status methods**

Add the following constants to the top of `src/live_coverage_bot/clients/slack.py` (alongside existing emoji constants):

```python
EMOJI_POWER = "\U0001f50b"       # 🔋
EMOJI_CRASH = "\U0001f4a5"       # 💥
EMOJI_RESTART = "\U0001f504"     # 🔄
```

Add these methods to the `SlackClient` class:

```python
def format_shutdown_message(self, now: datetime) -> str:
    """Format a graceful shutdown notification."""
    time_str = now.strftime("%H:%M UTC")
    return f"{EMOJI_POWER} Bot shutting down (manual stop). Last cycle: {time_str}"

def format_crash_message(self, error_type: str, error_msg: str) -> str:
    """Format a crash notification."""
    return f"{EMOJI_CRASH} Bot crashed: {error_type}: {error_msg}"

def format_recovery_summary(
    self,
    downtime_start: datetime,
    now: datetime,
    unmonitored_count: int,
    active_remaining: int,
) -> str:
    """Format the startup recovery summary after downtime."""
    gap_seconds = (now - downtime_start).total_seconds()
    hours = int(gap_seconds // 3600)
    minutes = int((gap_seconds % 3600) // 60)
    if hours > 0:
        gap_str = f"~{hours}h {minutes}min"
    else:
        gap_str = f"~{minutes}min"

    start_str = downtime_start.strftime("%a %b %d %H:%M")
    now_str = now.strftime("%a %b %d %H:%M")

    lines = [
        f"{EMOJI_RESTART} Bot restarting after downtime.",
        f"Offline: {start_str} \u2192 {now_str} UTC ({gap_str})",
        f"{unmonitored_count} events marked as unmonitored (kickoff occurred during downtime)",
        f"{active_remaining} active events carried over for normal monitoring",
    ]
    return "\n".join(lines)

async def post_bot_status(self, text: str) -> None:
    """Post a bot status message to the channel. Best-effort — logs errors."""
    try:
        response = await self._client.post(
            "/chat.postMessage",
            json={"channel": self._config.channel_id, "text": text},
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            logger.warning("Slack bot status post failed: %s", data.get("error"))
    except Exception as e:
        logger.warning("Failed to post bot status to Slack: %s", e)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_slack.py::TestBotStatusFormatting -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/clients/slack.py tests/test_slack.py
git commit -m "feat: add bot status formatting and posting to SlackClient"
```

---

### Task 6: Wire Up Heartbeat, Startup Recovery, and Crash Notification in MonitoringLoop

**Files:**
- Modify: `src/live_coverage_bot/core/loop.py:37-74`

- [ ] **Step 1: Add heartbeat write to end of _poll_cycle**

In `src/live_coverage_bot/core/loop.py`, at the end of the `_poll_cycle` method (after the cycle summary log on line 194), add the heartbeat write:

```python
# Update heartbeat
assert self._repo is not None
await self._repo.upsert_heartbeat(now)
```

- [ ] **Step 2: Add startup recovery and crash handling to the run method**

Replace the `run` method in `src/live_coverage_bot/core/loop.py` with:

```python
async def run(self) -> None:
    """Run the monitoring loop until interrupted."""
    db_path = self._settings.database.path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    self._db = Database(db_path)
    await self._db.initialize()
    self._repo = EventRepository(self._db)
    self._market_repo = MarketRepository(self._db)
    self._tracker = EventLifecycleTracker(
        self._repo,
        grace_period_minutes=self._settings.thresholds.grace_period_minutes,
        hard_timeout_minutes=self._settings.thresholds.hard_timeout_minutes,
    )

    async with (
        BetPawaClient(self._settings.betpawa) as betpawa,
        SlackClient(self._settings.slack) as slack,
    ):
        # Startup: detect downtime and recover
        now = datetime.now(tz=UTC)
        await self._startup_recovery(slack, now)

        logger.info(
            "Monitoring loop started (live: %ds, prematch: %ds)",
            self._settings.polling.live_interval_seconds,
            self._settings.polling.prematch_interval_seconds,
        )

        try:
            while True:
                try:
                    now = datetime.now(tz=UTC)
                    await self._poll_cycle(betpawa, slack, now=now)
                    await self._check_weekly_report(slack, now)
                    await self._cleanup_old_events(now)
                except (BetPawaError, SlackError) as e:
                    logger.warning("Error in poll cycle: %s", e)
                except Exception:
                    logger.exception("Unexpected error in poll cycle")

                await asyncio.sleep(self._settings.polling.live_interval_seconds)
        except Exception as e:
            # Crash notification — unhandled error that broke out of the loop
            logger.critical("Fatal error in monitoring loop: %s", e)
            crash_msg = slack.format_crash_message(
                type(e).__name__, str(e)
            )
            await slack.post_bot_status(crash_msg)
            raise

    await self._db.close()
```

- [ ] **Step 3: Add the _startup_recovery method**

Add to the `MonitoringLoop` class:

```python
async def _startup_recovery(self, slack: SlackClient, now: datetime) -> None:
    """Detect downtime from heartbeat gap and mark stale events."""
    assert self._repo is not None
    assert self._tracker is not None

    last_heartbeat = await self._repo.get_last_heartbeat()

    if last_heartbeat is not None:
        gap_seconds = (now - last_heartbeat).total_seconds()
        threshold = self._settings.polling.live_interval_seconds * 2

        if gap_seconds > threshold:
            logger.warning(
                "Downtime detected: last heartbeat %s (%.0fs ago)",
                last_heartbeat.strftime("%Y-%m-%d %H:%M UTC"),
                gap_seconds,
            )

            unmonitored_count = await self._tracker.mark_unmonitored(
                downtime_start=last_heartbeat, now=now
            )

            active_remaining = len(await self._repo.get_active_events())

            recovery_msg = slack.format_recovery_summary(
                downtime_start=last_heartbeat,
                now=now,
                unmonitored_count=unmonitored_count,
                active_remaining=active_remaining,
            )
            await slack.post_bot_status(recovery_msg)
        else:
            logger.info("Clean restart (last heartbeat %.0fs ago)", gap_seconds)
    else:
        logger.info("First run — no previous heartbeat found")

    # Record session start
    await self._repo.upsert_heartbeat(now, started_at=now)
```

- [ ] **Step 4: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass. The loop tests may use mocking — verify no breakage.

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/core/loop.py
git commit -m "feat: add heartbeat tracking, startup recovery, and crash notification to loop"
```

---

### Task 7: Add Graceful Shutdown Notification to __main__.py

**Files:**
- Modify: `src/live_coverage_bot/__main__.py:59-64`

- [ ] **Step 1: Update the main function to post shutdown message**

Replace the bot startup section in `src/live_coverage_bot/__main__.py` (lines 59-64) with:

```python
loop = MonitoringLoop(settings)
try:
    asyncio.run(loop.run())
except KeyboardInterrupt:
    logger.info("Shutdown requested, posting notification")
    try:
        asyncio.run(_post_shutdown_notification(settings))
    except Exception as e:
        logger.warning("Failed to send shutdown notification: %s", e)
    logger.info("Exiting")
```

- [ ] **Step 2: Add the shutdown notification helper**

Add a new async function after `_generate_report` in `__main__.py`:

```python
async def _post_shutdown_notification(settings) -> None:
    """Post a shutdown notification to Slack."""
    async with SlackClient(settings.slack) as slack:
        now = datetime.now(tz=UTC)
        msg = slack.format_shutdown_message(now)
        await slack.post_bot_status(msg)
```

- [ ] **Step 3: Add the missing import**

Add `SlackClient` to the imports at the top of `__main__.py`:

```python
from live_coverage_bot.clients.slack import SlackClient
```

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/__main__.py
git commit -m "feat: add graceful shutdown Slack notification"
```

---

### Task 8: Update Weekly Reports to Handle UNMONITORED

**Files:**
- Modify: `src/live_coverage_bot/core/reporter.py:62-132`
- Modify: `tests/test_reporter.py`

- [ ] **Step 1: Write tests for UNMONITORED in weekly report**

Add to `tests/test_reporter.py`, inside the existing `_insert_events` helper, add an UNMONITORED event. Then add a new test class:

First, update `_insert_events` to add an UNMONITORED event:

```python
async def _insert_events(repo: EventRepository):
    """Insert a mix of events for testing."""
    base = datetime(2026, 4, 14, tzinfo=UTC)
    pids_sr = [ProviderID(type=ProviderType.SPORTRADAR, id="100")]
    pids_gs = [ProviderID(type=ProviderType.GENIUSSPORTS, id="200")]

    events = [
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
        TrackedEvent(
            betpawa_event_id="3", home_team="E", away_team="F",
            competition="LaLiga", country="Spain",
            scheduled_kickoff=base, status=EventStatus.LIVE,
            provider_ids=pids_gs, first_seen_prematch=base,
            first_seen_live=base, transition_delay_sec=600,
        ),
        TrackedEvent(
            betpawa_event_id="4", home_team="G", away_team="H",
            competition="NPFL", country="Nigeria",
            scheduled_kickoff=base, status=EventStatus.NEVER_LIVE,
            provider_ids=pids_gs, first_seen_prematch=base,
        ),
        TrackedEvent(
            betpawa_event_id="5", home_team="I", away_team="J",
            competition="EPL", country="England",
            scheduled_kickoff=base, status=EventStatus.REMOVED,
            provider_ids=pids_sr, first_seen_prematch=base,
        ),
        TrackedEvent(
            betpawa_event_id="6", home_team="K", away_team="L",
            competition="EPL", country="England",
            scheduled_kickoff=base, status=EventStatus.UNMONITORED,
            provider_ids=pids_sr, first_seen_prematch=base,
        ),
        TrackedEvent(
            betpawa_event_id="7", home_team="M", away_team="N",
            competition="NPFL", country="Nigeria",
            scheduled_kickoff=base, status=EventStatus.UNMONITORED,
            provider_ids=pids_gs, first_seen_prematch=base,
        ),
    ]
    for e in events:
        await repo.insert_event(e)
```

Then add:

```python
class TestUnmonitoredInReport:
    async def test_summary_includes_unmonitored_line(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        assert "Unmonitored" in summary or "unmonitored" in summary
        assert "2" in summary  # 2 unmonitored events

    async def test_summary_total_includes_unmonitored(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        assert "Total prematch events tracked: 7" in summary

    async def test_unmonitored_excluded_from_problem_events(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        # UNMONITORED events should not appear in "Top competitions with most issues"
        # NPFL has 1 never_live (problem) but 1 unmonitored (not a problem)
        # If UNMONITORED were counted, NPFL would show "2" issues
        lines = summary.split("\n")
        npfl_lines = [l for l in lines if "NPFL" in l]
        for line in npfl_lines:
            assert "unmonitored" not in line.lower()

    async def test_csv_includes_unmonitored_status(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        csv_content = await reporter.generate_csv(start, end)
        assert "UNMONITORED" in csv_content

    async def test_unmonitored_excluded_from_provider_stats(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        # SPORTRADAR has 3 non-UNMONITORED events (ids 1,2,5) + 1 UNMONITORED (id 6)
        # Provider stats should show "3 events" not "4 events" for SPORTRADAR
        assert "SPORTRADAR" in summary
        lines = summary.split("\n")
        sr_line = next(l for l in lines if "SPORTRADAR" in l)
        assert "3 events" in sr_line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reporter.py::TestUnmonitoredInReport -v`
Expected: FAIL — existing report logic doesn't handle UNMONITORED

- [ ] **Step 3: Update generate_slack_summary to handle UNMONITORED**

In `src/live_coverage_bot/core/reporter.py`, update `generate_slack_summary`:

```python
async def generate_slack_summary(
    self, start: datetime, end: datetime
) -> str:
    """Generate the Slack-formatted weekly summary."""
    events = await self._repo.get_events_in_date_range(start, end)
    total = len(events)

    on_time = [e for e in events if e.status == EventStatus.LIVE and (e.transition_delay_sec or 0) < self._on_time_threshold]
    late = [e for e in events if e.status == EventStatus.LIVE and (e.transition_delay_sec or 0) >= self._on_time_threshold]
    never_live = [e for e in events if e.status == EventStatus.NEVER_LIVE]
    removed = [e for e in events if e.status == EventStatus.REMOVED]
    unmonitored = [e for e in events if e.status == EventStatus.UNMONITORED]

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
    if unmonitored:
        lines.append(
            f"\u26ab Unmonitored (bot offline): {len(unmonitored)} ({pct(len(unmonitored))})"
        )

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

    lines.append("")
    lines.append("By provider:")
    # Exclude UNMONITORED from provider stats
    monitored_events = [e for e in events if e.status != EventStatus.UNMONITORED]
    provider_stats = self._compute_provider_stats(monitored_events)
    for provider_type, stats in provider_stats.items():
        on_time_pct = f"{stats['on_time'] / stats['total'] * 100:.0f}%" if stats["total"] > 0 else "0%"
        avg_str = f"{stats['avg_delay']:.1f}min" if stats["avg_delay"] > 0 else "n/a"
        lines.append(
            f"  {provider_type}: {stats['total']} events, "
            f"{on_time_pct} on time, avg delay {avg_str}"
        )

    # Exclude UNMONITORED from problem events
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reporter.py -v`
Expected: All tests pass (including existing tests — update `test_generate_slack_summary` assertion from "Total prematch events tracked: 5" to "Total prematch events tracked: 7" since we added 2 UNMONITORED events to `_insert_events`)

- [ ] **Step 5: Update the existing test assertion**

In `tests/test_reporter.py`, update the existing test:

```python
async def test_generate_slack_summary(self, reporter, repo):
    await _insert_events(repo)
    start = datetime(2026, 4, 13, tzinfo=UTC)
    end = datetime(2026, 4, 15, tzinfo=UTC)
    summary = await reporter.generate_slack_summary(start, end)
    assert "Total prematch events tracked: 7" in summary
    assert "SPORTRADAR" in summary
    assert "GENIUSSPORTS" in summary
```

And update `test_generate_csv_content`:

```python
async def test_generate_csv_content(self, reporter, repo):
    await _insert_events(repo)
    start = datetime(2026, 4, 13, tzinfo=UTC)
    end = datetime(2026, 4, 15, tzinfo=UTC)
    csv_content = await reporter.generate_csv(start, end)
    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)
    assert len(rows) == 7
    assert "betpawa_event_id" in rows[0]
    assert "status" in rows[0]
```

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/live_coverage_bot/core/reporter.py tests/test_reporter.py
git commit -m "feat: handle UNMONITORED status in weekly reports"
```

---

### Task 9: Manual Integration Smoke Test

**Files:** None (testing only)

- [ ] **Step 1: Verify the bot starts cleanly with no previous heartbeat**

Run: `python -m live_coverage_bot --config config.yaml`
Expected in logs: `"First run — no previous heartbeat found"`
Kill with Ctrl+C after a few cycles.

- [ ] **Step 2: Verify clean restart detects no downtime**

Immediately restart: `python -m live_coverage_bot --config config.yaml`
Expected in logs: `"Clean restart (last heartbeat Xs ago)"`
Kill with Ctrl+C.

- [ ] **Step 3: Verify downtime recovery after a delay**

Wait 2+ minutes (or temporarily set `live_interval_seconds: 5` in config), then restart.
Expected in logs: `"Downtime detected"` and the recovery summary posted to Slack.

- [ ] **Step 4: Verify graceful shutdown posts notification**

Run the bot and Ctrl+C. Check Slack for the shutdown message.

- [ ] **Step 5: Final commit — all tasks complete**

Run the full test suite one last time:

```bash
pytest tests/ -v
```

Expected: All tests pass. No uncommitted changes remain.
