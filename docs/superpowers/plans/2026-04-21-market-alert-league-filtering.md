# Market Alert League Filtering — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Limit market anomaly Slack alerts to a configurable set of top-tier competitions while keeping full data collection and reporting for all leagues.

**Architecture:** Add `alert_competition_ids` whitelist to `MarketsConfig`. Thread `competition_id` through the data pipeline (API parser → client model → tracker → DB). Gate Slack posting in `_handle_market_comparison()` after the DB insert but before Slack calls.

**Tech Stack:** Python, Pydantic, aiosqlite, pytest

---

### Task 1: Add `alert_competition_ids` to config

**Files:**
- Modify: `src/live_coverage_bot/config/models.py:72-86`
- Modify: `tests/test_config.py:69-98`

- [ ] **Step 1: Write the failing test**

In `tests/test_config.py`, add to the existing `TestMarketsConfig` class:

```python
    def test_alert_competition_ids_defaults_to_empty(self):
        from live_coverage_bot.config.models import MarketsConfig

        config = MarketsConfig()
        assert config.alert_competition_ids == []

    def test_alert_competition_ids_accepts_list(self):
        from live_coverage_bot.config.models import MarketsConfig

        config = MarketsConfig(alert_competition_ids=["11965", "12097"])
        assert config.alert_competition_ids == ["11965", "12097"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::TestMarketsConfig::test_alert_competition_ids_defaults_to_empty -v`
Expected: FAIL — `MarketsConfig` has no field `alert_competition_ids`

- [ ] **Step 3: Write minimal implementation**

In `src/live_coverage_bot/config/models.py`, add the field to `MarketsConfig` (line 72). Insert as the first field in the class:

```python
class MarketsConfig(BaseModel):
    """Market comparison feature configuration."""

    enabled: bool = True
    alert_competition_ids: list[str] = []
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: ALL PASS (including existing tests — default `[]` doesn't break anything)

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/config/models.py tests/test_config.py
git commit -m "Add alert_competition_ids config field to MarketsConfig"
```

---

### Task 2: Add `competition_id` to `UpcomingEvent` and parser

**Files:**
- Modify: `src/live_coverage_bot/clients/models.py:23-36`
- Modify: `src/live_coverage_bot/clients/parsers.py:199-251`
- Modify: `tests/test_parsers.py:270-335`

- [ ] **Step 1: Write the failing tests**

In `tests/test_parsers.py`, add to the existing `TestParseUpcomingEvent` class:

```python
    def test_extracts_competition_id(self):
        data = self._make_event_data()
        data["competition"] = {"id": "12546", "name": "UEFA Europa League"}
        event = parse_upcoming_event(data)
        assert event is not None
        assert event.competition_id == "12546"

    def test_missing_competition_id_defaults_to_empty(self):
        data = self._make_event_data()
        data["competition"] = {"name": "Some League"}
        event = parse_upcoming_event(data)
        assert event is not None
        assert event.competition_id == ""

    def test_null_competition_gives_empty_competition_id(self):
        data = self._make_event_data()
        data["competition"] = None
        event = parse_upcoming_event(data)
        assert event is not None
        assert event.competition_id == ""
```

Also update `_make_event_data` to include a competition id for consistency with the real API:

```python
    def _make_event_data(self, **overrides) -> dict:
        data = {
            "id": "55001",
            "startTime": "2026-04-17T18:30:00Z",
            "participants": [
                {"position": 1, "name": "Liverpool"},
                {"position": 2, "name": "ManCity"},
            ],
            "competition": {"id": "11965", "name": "Premier League"},
            "region": {"name": "England"},
            "widgets": [],
        }
        data.update(overrides)
        return data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_parsers.py::TestParseUpcomingEvent::test_extracts_competition_id -v`
Expected: FAIL — `UpcomingEvent` has no field `competition_id`

- [ ] **Step 3: Write minimal implementation**

In `src/live_coverage_bot/clients/models.py`, add `competition_id` to `UpcomingEvent`:

```python
class UpcomingEvent(BaseModel):
    """Upcoming event data from BetPawa pre-match API.

    Contains full event details for human-readable logging during cache refresh.
    """

    event_id: str  # BetPawa event ID (without bp: prefix)
    home_team: str
    away_team: str
    competition_name: str
    competition_id: str = ""
    country_name: str | None = None
    start_time: datetime
    provider_ids: list["ProviderID"]  # Preserves BOTH SPORTRADAR and GENIUSSPORTS
```

In `src/live_coverage_bot/clients/parsers.py`, in `parse_upcoming_event()`, extract `competition.id` (around line 234):

```python
    # Get competition and region
    competition = event_data.get("competition") or {}
    competition_name = competition.get("name", "")
    competition_id = str(competition.get("id", ""))
    region = event_data.get("region") or {}
    country_name = region.get("name") or None
```

And pass it to the constructor (around line 243):

```python
    return UpcomingEvent(
        event_id=str(event_id),
        home_team=home_team,
        away_team=away_team,
        competition_name=competition_name,
        competition_id=competition_id,
        country_name=country_name,
        start_time=start_time,
        provider_ids=provider_ids,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_parsers.py::TestParseUpcomingEvent -v`
Expected: ALL PASS

Also run existing parser tests to check nothing broke:

Run: `python -m pytest tests/test_parsers.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/clients/models.py src/live_coverage_bot/clients/parsers.py tests/test_parsers.py
git commit -m "Add competition_id to UpcomingEvent and parser"
```

---

### Task 3: Thread `competition_id` through tracker feed and into DB

**Files:**
- Modify: `src/live_coverage_bot/clients/betpawa.py:296-309`
- Modify: `src/live_coverage_bot/models/events.py:29-48`
- Modify: `src/live_coverage_bot/core/tracker.py:39-49`
- Modify: `src/live_coverage_bot/db/connection.py:82-92` (migrations)
- Modify: `src/live_coverage_bot/db/repository.py:20-46` (insert)
- Modify: `src/live_coverage_bot/db/repository.py:217-237` (_row_to_event)
- Modify: `tests/test_loop.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_loop.py`, add a new test to the `TestPollCycleLogic` class:

```python
    async def test_prematch_event_stores_competition_id(self, db, settings):
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

        mock_betpawa = AsyncMock()
        mock_betpawa.get_upcoming_events.return_value = [
            UpcomingEvent(
                event_id="99001",
                home_team="Arsenal",
                away_team="Chelsea",
                competition_name="Premier League",
                competition_id="11965",
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
        assert event.competition_id == "11965"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_loop.py::TestPollCycleLogic::test_prematch_event_stores_competition_id -v`
Expected: FAIL — `TrackedEvent` has no field `competition_id`

- [ ] **Step 3: Write minimal implementation**

**3a.** In `src/live_coverage_bot/models/events.py`, add `competition_id` to `TrackedEvent` (after `competition` field, around line 36):

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
    created_at: datetime | None = None
    updated_at: datetime | None = None
```

**3b.** In `src/live_coverage_bot/clients/betpawa.py`, pass `competition_id` in `upcoming_to_tracker_feed()` (line 296-309):

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
                "competition_id": event.competition_id,
                "country": event.country_name,
                "scheduled_kickoff": event.start_time,
                "provider_ids": event.provider_ids,
            }
            for event in events
        ]
```

**3c.** In `src/live_coverage_bot/core/tracker.py`, set `competition_id` in `register_prematch_events()` (line 39-49):

```python
            event = TrackedEvent(
                betpawa_event_id=bp_id,
                home_team=item["home_team"],
                away_team=item["away_team"],
                competition=item["competition"],
                competition_id=item.get("competition_id", ""),
                country=item.get("country"),
                scheduled_kickoff=item["scheduled_kickoff"],
                status=EventStatus.PREMATCH,
                provider_ids=item["provider_ids"],
                first_seen_prematch=now,
            )
```

**3d.** In `src/live_coverage_bot/db/connection.py`, add migration (append to `MIGRATIONS_SQL`, around line 92):

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
"""
```

**3e.** In `src/live_coverage_bot/db/repository.py`, include `competition_id` in `insert_event()` (line 20-46):

```python
    async def insert_event(self, event: TrackedEvent) -> int:
        """Insert a new tracked event. Returns the row ID."""
        cursor = await self._db.execute(
            """INSERT INTO events
            (betpawa_event_id, home_team, away_team, competition, competition_id, country,
             scheduled_kickoff, status, provider_ids, first_seen_prematch,
             first_seen_live, transition_delay_sec, slack_message_ts,
             removed_at, pre_removal_market_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            ),
        )
        return cursor.lastrowid
```

And in `_row_to_event()` (line 217-237):

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
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_loop.py::TestPollCycleLogic::test_prematch_event_stores_competition_id -v`
Expected: PASS

Run full suite to check nothing broke:

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/clients/betpawa.py src/live_coverage_bot/models/events.py src/live_coverage_bot/core/tracker.py src/live_coverage_bot/db/connection.py src/live_coverage_bot/db/repository.py tests/test_loop.py
git commit -m "Thread competition_id through data pipeline into DB"
```

---

### Task 4: Add market alert league gate in `_handle_market_comparison`

**Files:**
- Modify: `src/live_coverage_bot/core/loop.py:354-393`
- Create: `tests/test_market_alert_filter.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_market_alert_filter.py`:

```python
"""Tests for market alert league filtering."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.config.models import (
    DatabaseConfig,
    MarketsConfig,
    PollingConfig,
    ReportingConfig,
    Settings,
    SlackConfig,
    ThresholdConfig,
)
from live_coverage_bot.core.loop import MonitoringLoop
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.models.markets import SnapshotPhase


def _make_settings(**markets_overrides) -> Settings:
    markets_kwargs = {"enabled": True}
    markets_kwargs.update(markets_overrides)
    return Settings(
        polling=PollingConfig(),
        thresholds=ThresholdConfig(),
        slack=SlackConfig(bot_token="xoxb-test", channel_id="C123"),
        database=DatabaseConfig(path=":memory:"),
        reporting=ReportingConfig(),
        markets=MarketsConfig(**markets_kwargs),
        _env_file=None,
    )


async def _insert_test_event(
    repo: EventRepository, competition_id: str = "11965"
) -> TrackedEvent:
    """Insert a test event and return it with the DB-assigned id."""
    event = TrackedEvent(
        betpawa_event_id="99001",
        home_team="Arsenal",
        away_team="Chelsea",
        competition="Premier League",
        competition_id=competition_id,
        country="England",
        scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
        status=EventStatus.LIVE,
        provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
        first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        first_seen_live=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
    )
    row_id = await repo.insert_event(event)
    event.id = row_id
    return event


class TestMarketAlertLeagueFilter:
    async def test_skips_slack_when_competition_not_in_whitelist(self, db):
        """Market comparison is saved to DB but Slack alert is NOT sent."""
        settings = _make_settings(alert_competition_ids=["12097"])  # Serie A only
        repo = EventRepository(db)
        market_repo = MarketRepository(db)

        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo
        loop._market_repo = market_repo

        event = await _insert_test_event(repo, competition_id="11965")  # Premier League

        mock_slack = AsyncMock()
        now = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)

        with patch.object(loop, "_handle_initial_comparison", new_callable=AsyncMock) as mock_initial, \
             patch.object(loop, "_handle_followup_comparison", new_callable=AsyncMock) as mock_followup, \
             patch("live_coverage_bot.core.loop.compare_snapshots") as mock_compare, \
             patch.object(market_repo, "get_latest_prematch_snapshot", new_callable=AsyncMock) as mock_pre, \
             patch.object(market_repo, "get_snapshots_for_event", new_callable=AsyncMock) as mock_snaps, \
             patch.object(market_repo, "insert_comparison", new_callable=AsyncMock):

            # Set up mocks so comparison proceeds
            mock_pre_snap = AsyncMock()
            mock_pre_snap.total_market_count = 100
            mock_pre.return_value = mock_pre_snap

            mock_live_snap = AsyncMock()
            mock_live_snap.phase = SnapshotPhase.LIVE_0
            mock_snaps.return_value = [mock_live_snap]

            mock_cmp = AsyncMock()
            mock_cmp.snapshot_phase = None
            mock_compare.return_value = mock_cmp

            await loop._handle_market_comparison(mock_slack, event.id, SnapshotPhase.LIVE_0, now)

            # Comparison was saved to DB
            market_repo.insert_comparison.assert_called_once()
            # But Slack handlers were NOT called
            mock_initial.assert_not_called()
            mock_followup.assert_not_called()

    async def test_posts_slack_when_competition_in_whitelist(self, db):
        """Market comparison is saved AND Slack alert IS sent."""
        settings = _make_settings(alert_competition_ids=["11965"])  # Premier League
        repo = EventRepository(db)
        market_repo = MarketRepository(db)

        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo
        loop._market_repo = market_repo

        event = await _insert_test_event(repo, competition_id="11965")

        mock_slack = AsyncMock()
        now = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)

        with patch.object(loop, "_handle_initial_comparison", new_callable=AsyncMock) as mock_initial, \
             patch("live_coverage_bot.core.loop.compare_snapshots") as mock_compare, \
             patch.object(market_repo, "get_latest_prematch_snapshot", new_callable=AsyncMock) as mock_pre, \
             patch.object(market_repo, "get_snapshots_for_event", new_callable=AsyncMock) as mock_snaps, \
             patch.object(market_repo, "insert_comparison", new_callable=AsyncMock):

            mock_pre_snap = AsyncMock()
            mock_pre_snap.total_market_count = 100
            mock_pre.return_value = mock_pre_snap

            mock_live_snap = AsyncMock()
            mock_live_snap.phase = SnapshotPhase.LIVE_0
            mock_snaps.return_value = [mock_live_snap]

            mock_cmp = AsyncMock()
            mock_cmp.snapshot_phase = None
            mock_compare.return_value = mock_cmp

            await loop._handle_market_comparison(mock_slack, event.id, SnapshotPhase.LIVE_0, now)

            # Slack handler WAS called
            mock_initial.assert_called_once()

    async def test_empty_whitelist_alerts_everything(self, db):
        """Empty alert_competition_ids means no filtering — all alerts go through."""
        settings = _make_settings(alert_competition_ids=[])  # Empty = alert all
        repo = EventRepository(db)
        market_repo = MarketRepository(db)

        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = repo
        loop._market_repo = market_repo

        event = await _insert_test_event(repo, competition_id="99999")  # Random league

        mock_slack = AsyncMock()
        now = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)

        with patch.object(loop, "_handle_initial_comparison", new_callable=AsyncMock) as mock_initial, \
             patch("live_coverage_bot.core.loop.compare_snapshots") as mock_compare, \
             patch.object(market_repo, "get_latest_prematch_snapshot", new_callable=AsyncMock) as mock_pre, \
             patch.object(market_repo, "get_snapshots_for_event", new_callable=AsyncMock) as mock_snaps, \
             patch.object(market_repo, "insert_comparison", new_callable=AsyncMock):

            mock_pre_snap = AsyncMock()
            mock_pre_snap.total_market_count = 100
            mock_pre.return_value = mock_pre_snap

            mock_live_snap = AsyncMock()
            mock_live_snap.phase = SnapshotPhase.LIVE_0
            mock_snaps.return_value = [mock_live_snap]

            mock_cmp = AsyncMock()
            mock_cmp.snapshot_phase = None
            mock_compare.return_value = mock_cmp

            await loop._handle_market_comparison(mock_slack, event.id, SnapshotPhase.LIVE_0, now)

            # Slack handler called — empty whitelist means alert everything
            mock_initial.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_market_alert_filter.py -v`
Expected: FAIL — the gate doesn't exist yet, so the "skips" test will fail (Slack handlers will be called when they shouldn't be)

- [ ] **Step 3: Write minimal implementation**

In `src/live_coverage_bot/core/loop.py`, add the gate in `_handle_market_comparison()`. Insert after the event fetch (line 383-385) and before the Slack routing (line 387):

Replace this block (lines 383-393):

```python
        event = await self._repo.get_by_id(event_id)
        if event is None:
            return

        try:
            if phase == SnapshotPhase.LIVE_0:
                await self._handle_initial_comparison(slack, event, cmp, live_snap, now)
            elif phase in (SnapshotPhase.LIVE_2, SnapshotPhase.LIVE_5):
                await self._handle_followup_comparison(slack, event, cmp, live_snap, phase, now)
        except SlackError as e:
            logger.warning("Slack market alert failed for %s: %s", event.betpawa_event_id, e)
```

With:

```python
        event = await self._repo.get_by_id(event_id)
        if event is None:
            return

        # Gate: skip Slack alert if competition not in whitelist
        alert_ids = self._settings.markets.alert_competition_ids
        if alert_ids and event.competition_id not in alert_ids:
            logger.debug(
                "Skipping market alert for %s (%s) — not in alert leagues",
                event.betpawa_event_id, event.competition,
            )
            return

        try:
            if phase == SnapshotPhase.LIVE_0:
                await self._handle_initial_comparison(slack, event, cmp, live_snap, now)
            elif phase in (SnapshotPhase.LIVE_2, SnapshotPhase.LIVE_5):
                await self._handle_followup_comparison(slack, event, cmp, live_snap, phase, now)
        except SlackError as e:
            logger.warning("Slack market alert failed for %s: %s", event.betpawa_event_id, e)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_market_alert_filter.py -v`
Expected: ALL PASS

Run full suite:

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/core/loop.py tests/test_market_alert_filter.py
git commit -m "Gate market anomaly Slack alerts by competition whitelist"
```

---

### Task 5: Add competition IDs to `config.yaml`

**Files:**
- Modify: `config.yaml`

- [ ] **Step 1: Add the whitelist to config.yaml**

Add `alert_competition_ids` to the `markets:` section in `config.yaml`, after `enabled: true`:

```yaml
markets:
  enabled: true
  alert_competition_ids:
    - "11965"   # Premier League (England)
    - "12145"   # FA Cup (England)
    - "12097"   # Serie A (Italy)
    - "12243"   # Coppa Italia (Italy)
    - "12468"   # Super Cup (Italy)
    - "11998"   # Coupe de France (France)
    - "12127"   # Ligue 1 (France)
    - "11979"   # Copa del Rey (Spain)
    - "12039"   # LaLiga (Spain)
    - "12223"   # Super Cup (Spain)
    - "12110"   # Bundesliga (Germany)
    - "12389"   # DFB Pokal (Germany)
    - "12541"   # UEFA Champions League (International)
    - "12546"   # UEFA Europa League (International)
    - "12545"   # UEFA Europa Conference League (International)
    - "258194"  # FIFA World Cup (International)
  alert_thresholds:
    retention_below_pct: 60
    any_key_market_dropped: true
    max_odds_shift_pct: 30
  odds_shift_display_threshold: 15
  live_snapshot_offsets_minutes: [0, 2, 5]
  key_markets:
    - "1X2 - FT"
    - "Total Score Over/Under - FT"
    - "Both Teams To Score - FT"
    - "Double Chance - FT"
    - "1X2 - 1H"
```

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add config.yaml
git commit -m "Add top-league competition IDs to config.yaml"
```
