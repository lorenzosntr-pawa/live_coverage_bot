# Code Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up code quality issues so the upcoming monitoring improvements build on solid, well-typed, well-structured code.

**Architecture:** Refactor in place — no new features, no behavior changes. Extract types, split responsibilities, standardize patterns. Every task produces passing tests.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, pytest-asyncio, ruff

---

### Task 1: Type the Tracker Return Values

Create `TransitionResult` and `RemovedResult` dataclasses to replace `dict[str, Any]` returns in the tracker and loop.

**Files:**
- Modify: `src/live_coverage_bot/models/events.py`
- Modify: `src/live_coverage_bot/core/tracker.py`
- Modify: `src/live_coverage_bot/core/loop.py`
- Test: `tests/test_tracker.py`

- [ ] **Step 1: Add TransitionResult and RemovedResult to events model**

In `src/live_coverage_bot/models/events.py`, add after the `StateChange` class:

```python
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
```

- [ ] **Step 2: Write test for typed tracker returns**

Add to `tests/test_tracker.py`:

```python
from live_coverage_bot.models.events import TransitionResult, RemovedResult


class TestTypedReturns:
    async def test_check_transitions_returns_transition_results(self, db):
        from live_coverage_bot.db.repository import EventRepository
        from live_coverage_bot.core.tracker import EventLifecycleTracker
        from live_coverage_bot.models.events import EventStatus, TrackedEvent
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo)
        event = TrackedEvent(
            betpawa_event_id="100",
            home_team="A",
            away_team="B",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="1")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        await repo.insert_event(event)

        now = datetime(2026, 4, 15, 15, 6, tzinfo=UTC)
        results = await tracker.check_transitions(set(), now=now)
        assert len(results) == 1
        assert isinstance(results[0], TransitionResult)
        assert results[0].new_status == EventStatus.LATE
        assert results[0].old_status == EventStatus.PREMATCH

    async def test_detect_removed_returns_removed_results(self, db):
        from live_coverage_bot.db.repository import EventRepository
        from live_coverage_bot.core.tracker import EventLifecycleTracker
        from live_coverage_bot.models.events import EventStatus, TrackedEvent
        from live_coverage_bot.clients.models import ProviderID, ProviderType
        from datetime import UTC, datetime

        repo = EventRepository(db)
        tracker = EventLifecycleTracker(repo)
        event = TrackedEvent(
            betpawa_event_id="200",
            home_team="C",
            away_team="D",
            competition="Test",
            scheduled_kickoff=datetime(2026, 4, 15, 16, 0, tzinfo=UTC),
            status=EventStatus.PREMATCH,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="2")],
            first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        await repo.insert_event(event)

        now = datetime(2026, 4, 15, 15, 0, tzinfo=UTC)
        results = await tracker.detect_removed(set(), now=now)
        assert len(results) == 1
        assert isinstance(results[0], RemovedResult)
        assert results[0].old_status == EventStatus.PREMATCH
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_tracker.py::TestTypedReturns -v`
Expected: FAIL — `TransitionResult` / `RemovedResult` not returned yet.

- [ ] **Step 4: Update tracker to return typed results**

In `src/live_coverage_bot/core/tracker.py`:

1. Update imports at top:
```python
from live_coverage_bot.models.events import (
    EventStatus,
    RemovedResult,
    TrackedEvent,
    TransitionResult,
)
```

2. Change `check_transitions` return type and all dict constructions:
```python
async def check_transitions(
    self,
    live_betpawa_ids: set[str],
    now: datetime,
) -> list[TransitionResult]:
```

Replace every `{"betpawa_event_id": ..., "event": event, ...}` dict with the equivalent `TransitionResult(...)`. For example, the REMOVED→LIVE recovery (around line 103):

```python
transitions.append(TransitionResult(
    event=event,
    old_status=EventStatus.REMOVED,
    new_status=EventStatus.LIVE,
    delay_sec=delay_sec,
    details="Recovered: appeared in live feed after being marked REMOVED",
))
```

3. Change `_evaluate_transition` return type:
```python
async def _evaluate_transition(
    self,
    event: TrackedEvent,
    is_in_live_feed: bool,
    now: datetime,
) -> TransitionResult | None:
```

Replace all dict returns with `TransitionResult(...)`. For example the PREMATCH→LIVE case:
```python
return TransitionResult(
    event=event,
    old_status=old_status,
    new_status=EventStatus.LIVE,
    delay_sec=delay_sec,
    details=f"Went live ({delay_sec // 60}min after kickoff)" if delay_sec > 0 else "Went live on time",
)
```

The PREMATCH→LATE case:
```python
return TransitionResult(
    event=event,
    old_status=old_status,
    new_status=EventStatus.LATE,
    details=f"{int(elapsed_min)}min past kickoff",
)
```

The LATE→LIVE case:
```python
return TransitionResult(
    event=event,
    old_status=old_status,
    new_status=EventStatus.LIVE,
    delay_sec=delay_sec,
    details=f"Went live (delay: {delay_sec // 60}min)",
)
```

The LATE→NEVER_LIVE case:
```python
return TransitionResult(
    event=event,
    old_status=old_status,
    new_status=EventStatus.NEVER_LIVE,
    details=f"Timed out after {int(elapsed_min)}min",
)
```

4. Change `detect_removed` return type:
```python
async def detect_removed(
    self,
    current_prematch_ids: set[str],
    now: datetime,
    kickoff_buffer_minutes: int = 30,
) -> list[RemovedResult]:
```

Replace the dict:
```python
removed.append(RemovedResult(
    event=event,
    old_status=EventStatus.PREMATCH,
    details="Disappeared from prematch feed before kickoff",
))
```

- [ ] **Step 5: Update loop.py to use typed attributes**

In `src/live_coverage_bot/core/loop.py`:

1. In `_poll_cycle`, update the live transition ID extraction (around line 124):
```python
live_transition_bp_ids: set[str] = {
    t.event.betpawa_event_id
    for t in transitions
    if t.new_status == EventStatus.LIVE
}
```

2. In `_handle_transition`, update the signature and body (around line 162):
```python
async def _handle_transition(
    self, slack: SlackClient, transition: TransitionResult, now: datetime
) -> None:
    """Send Slack alerts/updates for a state transition."""
    assert self._repo is not None
    event = transition.event
    old_status = transition.old_status
    new_status = transition.new_status

    refreshed = await self._repo.get_by_betpawa_id(event.betpawa_event_id)
    if refreshed is None:
        return

    try:
        if old_status == EventStatus.PREMATCH and new_status == EventStatus.LATE:
            ts = await slack.post_alert(refreshed, now)
            assert refreshed.id is not None
            await self._repo.update_slack_ts(refreshed.id, ts)
            logger.info(
                "Alert sent: %s %s vs %s (LATE)",
                refreshed.betpawa_event_id, refreshed.home_team, refreshed.away_team,
            )

        elif new_status in (EventStatus.LIVE, EventStatus.NEVER_LIVE):
            if refreshed.slack_message_ts:
                await slack.update_message(refreshed.slack_message_ts, refreshed, now)
                delay_sec = transition.delay_sec
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
                    "Updated alert: %s %s vs %s (%s -> %s)",
                    refreshed.betpawa_event_id, refreshed.home_team,
                    refreshed.away_team, old_status, new_status,
                )

    except SlackError as e:
        logger.warning("Slack operation failed for %s: %s", refreshed.betpawa_event_id, e)
```

Add `TransitionResult` to the imports at the top of `loop.py`:
```python
from live_coverage_bot.models.events import EventStatus, TransitionResult
```

- [ ] **Step 6: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/models/events.py src/live_coverage_bot/core/tracker.py src/live_coverage_bot/core/loop.py tests/test_tracker.py
git commit -m "refactor: replace dict[str, Any] with TransitionResult/RemovedResult dataclasses"
```

---

### Task 2: Type MarketComparison Details

Replace the untyped `details: dict` on `MarketComparison` with structured dataclasses.

**Files:**
- Modify: `src/live_coverage_bot/models/markets.py`
- Modify: `src/live_coverage_bot/core/market_comparator.py`
- Modify: `src/live_coverage_bot/db/market_repository.py`
- Modify: `src/live_coverage_bot/clients/slack.py`
- Modify: `src/live_coverage_bot/core/market_reporter.py`
- Test: `tests/test_market_comparator.py`

- [ ] **Step 1: Add typed detail classes to markets model**

In `src/live_coverage_bot/models/markets.py`, add after the `Market` class and before `MarketSnapshot`:

```python
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
```

Then change `MarketComparison.details` type:
```python
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
```

- [ ] **Step 2: Write test for typed details**

Add to `tests/test_market_comparator.py`:

```python
from live_coverage_bot.models.markets import ComparisonDetails, DroppedMarketDetail, OddsShiftDetail


class TestTypedDetails:
    def test_details_has_typed_dropped(self):
        pre = _snap(1, SnapshotPhase.PREMATCH_1, [_market_1x2(), _market_btts()])
        live = _snap(1, SnapshotPhase.LIVE, [_market_1x2()])
        cfg = _default_config()

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert isinstance(cmp.details, ComparisonDetails)
        assert len(cmp.details.dropped) == 1
        assert isinstance(cmp.details.dropped[0], DroppedMarketDetail)
        assert cmp.details.dropped[0].market_type_name == "Both Teams To Score - FT"

    def test_details_has_typed_odds_shifts(self):
        pre = _snap(1, SnapshotPhase.PREMATCH_1, [_market_1x2(home_odds=2.0)])
        live = _snap(1, SnapshotPhase.LIVE, [_market_1x2(home_odds=2.5)])
        cfg = _default_config()

        cmp = compare_snapshots(pre, live, cfg, now=datetime(2026, 4, 15, 15, 1, tzinfo=UTC))
        assert len(cmp.details.odds_shifts) > 0
        shift = cmp.details.odds_shifts[0]
        assert isinstance(shift, OddsShiftDetail)
        assert shift.prematch_price == 2.0
        assert shift.live_price == 2.5
        assert shift.shift_pct > 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_market_comparator.py::TestTypedDetails -v`
Expected: FAIL — `ComparisonDetails` not yet used in comparator.

- [ ] **Step 4: Update market_comparator to build typed details**

In `src/live_coverage_bot/core/market_comparator.py`, update imports:
```python
from live_coverage_bot.models.markets import (
    AddedMarketDetail,
    ComparisonDetails,
    DroppedMarketDetail,
    Market,
    MarketComparison,
    MarketSnapshot,
    OddsShiftDetail,
)
```

Replace the `dropped_names` / `added_names` / `odds_shifts` construction (lines 30–63) with:

```python
dropped_details = [
    DroppedMarketDetail(market_type_id=mt_id, market_type_name=pre_by_id[mt_id].market_type_name)
    for mt_id in dropped_ids
]
added_details = [
    AddedMarketDetail(market_type_id=mt_id, market_type_name=live_by_id[mt_id].market_type_name)
    for mt_id in added_ids
]

key_markets = set(config.key_markets)
dropped_key_markets = [d.market_type_name for d in dropped_details if d.market_type_name in key_markets]

odds_shifts: list[OddsShiftDetail] = []
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
            odds_shifts.append(OddsShiftDetail(
                market_type_name=pre_m.market_type_name,
                selection_name=pre_sel.name,
                selection_type_id=pre_sel.type_id,
                handicap=pre_row.handicap,
                prematch_price=pre_sel.price,
                live_price=live_sel.price,
                shift_pct=round(shift, 2),
            ))
```

Replace the `details=` dict in the return with:
```python
details=ComparisonDetails(
    dropped=dropped_details,
    added=added_details,
    odds_shifts=odds_shifts,
),
```

- [ ] **Step 5: Update market_repository serialization**

In `src/live_coverage_bot/db/market_repository.py`, update `insert_comparison` (line 94) to serialize `ComparisonDetails`:

```python
json.dumps(cmp.details.model_dump(), separators=(",", ":"))
```

Update `_row_to_comparison` (line 142) to deserialize:

```python
from live_coverage_bot.models.markets import ComparisonDetails
```

Replace `details=json.loads(row["details_json"])` with:
```python
details=ComparisonDetails(**json.loads(row["details_json"])),
```

- [ ] **Step 6: Update slack.py to use typed details**

In `src/live_coverage_bot/clients/slack.py`, update `format_market_recap` (lines 176-198):

Replace `dropped_names = comparison.details.get("dropped", [])` with:
```python
dropped_names = [d.market_type_name for d in comparison.details.dropped]
```

Replace `odds_shifts = comparison.details.get("odds_shifts", [])` and the biggest-shift block with:
```python
odds_shifts = comparison.details.odds_shifts
if odds_shifts:
    biggest = max(odds_shifts, key=lambda s: s.shift_pct)
    lines.append(
        f" \u2022 Biggest odds shift: {biggest.market_type_name} "
        f"{biggest.selection_name} "
        f"{biggest.prematch_price} \u2192 {biggest.live_price} "
        f"({biggest.shift_pct:+.1f}%)"
    )
```

- [ ] **Step 7: Update market_reporter.py to use typed details**

In `src/live_coverage_bot/core/market_reporter.py`, update `generate_markets_summary_block` (line 112):

Replace `for name in c.details.get("dropped", []):` with:
```python
for d in c.details.dropped:
    drop_count[d.market_type_name] += 1
```

- [ ] **Step 8: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 9: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/models/markets.py src/live_coverage_bot/core/market_comparator.py src/live_coverage_bot/db/market_repository.py src/live_coverage_bot/clients/slack.py src/live_coverage_bot/core/market_reporter.py tests/test_market_comparator.py
git commit -m "refactor: type MarketComparison.details with ComparisonDetails dataclass"
```

---

### Task 3: Extract Shared Slack Formatting

**Files:**
- Modify: `src/live_coverage_bot/clients/slack.py`
- Test: `tests/test_slack.py`

- [ ] **Step 1: Write test for the extracted helper**

Add to `tests/test_slack.py`:

```python
class TestEventHeaderHelper:
    def test_format_event_header(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        provider_str, competition_line, kickoff_str = client._format_event_header(sample_event)
        assert "SPORTRADAR #12345" in provider_str
        assert "England Premier League | England" == competition_line
        assert "15:00 UTC" == kickoff_str

    def test_format_event_header_no_country(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.country = None
        provider_str, competition_line, kickoff_str = client._format_event_header(sample_event)
        assert competition_line == "England Premier League"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_slack.py::TestEventHeaderHelper -v`
Expected: FAIL — `_format_event_header` does not exist yet.

- [ ] **Step 3: Extract _format_event_header and use it**

In `src/live_coverage_bot/clients/slack.py`, add the helper method to `SlackClient`:

```python
def _format_event_header(
    self, event: TrackedEvent
) -> tuple[str, str, str]:
    """Extract common header fields: (provider_str, competition_line, kickoff_str)."""
    provider_str = ", ".join(
        f"{p.type} #{p.id}" for p in event.provider_ids
    )
    competition_line = event.competition
    if event.country:
        competition_line += f" | {event.country}"
    kickoff_str = event.scheduled_kickoff.strftime("%H:%M UTC")
    return provider_str, competition_line, kickoff_str
```

Then update `format_parent_message` — replace lines 38-45 with:
```python
provider_str, competition_line, kickoff_str = self._format_event_header(event)
```

And update `format_market_anomaly_parent` — replace lines 208-212 with:
```python
provider_str, competition_line, kickoff_str = self._format_event_header(event)
```

- [ ] **Step 4: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS (existing formatting tests still pass, new helper tests pass).

- [ ] **Step 5: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/clients/slack.py tests/test_slack.py
git commit -m "refactor: extract _format_event_header to deduplicate Slack formatting"
```

---

### Task 4: Extract BetPawa Parsers

Split parsing logic out of `BetPawaClient` into a dedicated `clients/parsers.py`.

**Files:**
- Create: `src/live_coverage_bot/clients/parsers.py`
- Modify: `src/live_coverage_bot/clients/betpawa.py`
- Create: `tests/test_parsers.py`

- [ ] **Step 1: Write tests for standalone parser functions**

Create `tests/test_parsers.py`:

```python
"""Tests for BetPawa API response parsers."""

from datetime import UTC, datetime

from live_coverage_bot.clients.models import ProviderType
from live_coverage_bot.clients.parsers import (
    extract_provider_ids,
    extract_scores,
    parse_event_markets,
    parse_live_event,
    parse_upcoming_event,
)


class TestExtractProviderIds:
    def test_extracts_sportradar_and_geniussports(self):
        widgets = [
            {"id": "123", "type": "SPORTRADAR"},
            {"id": "456", "type": "GENIUSSPORTS"},
        ]
        ids = extract_provider_ids(widgets)
        assert len(ids) == 2
        assert ids[0].type == ProviderType.SPORTRADAR
        assert ids[0].id == "123"
        assert ids[1].type == ProviderType.GENIUSSPORTS

    def test_deduplicates(self):
        widgets = [
            {"id": "123", "type": "SPORTRADAR"},
            {"id": "123", "type": "SPORTRADAR"},
        ]
        ids = extract_provider_ids(widgets)
        assert len(ids) == 1

    def test_ignores_unknown_types(self):
        widgets = [{"id": "1", "type": "UNKNOWN_PROVIDER"}]
        ids = extract_provider_ids(widgets)
        assert len(ids) == 0


class TestExtractScores:
    def test_extracts_ft_scores(self):
        results = {
            "participantPeriodResults": [
                {
                    "participant": {"id": "1", "type": "HOME"},
                    "periodResults": [
                        {"period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"}, "result": "2", "type": "SCORE"},
                    ],
                },
                {
                    "participant": {"id": "2", "type": "AWAY"},
                    "periodResults": [
                        {"period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"}, "result": "1", "type": "SCORE"},
                    ],
                },
            ]
        }
        home, away = extract_scores(results)
        assert home == 2
        assert away == 1

    def test_returns_none_for_empty_results(self):
        home, away = extract_scores({})
        assert home is None
        assert away is None

    def test_logs_warning_on_invalid_score(self):
        results = {
            "participantPeriodResults": [
                {
                    "participant": {"id": "1", "type": "HOME"},
                    "periodResults": [
                        {"period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"}, "result": "abc", "type": "SCORE"},
                    ],
                },
            ]
        }
        home, away = extract_scores(results)
        assert home is None


class TestParseLiveEvent:
    def test_parses_minimal_event(self):
        data = {
            "id": "12345",
            "participants": [
                {"position": 1, "name": "Team A"},
                {"position": 2, "name": "Team B"},
            ],
            "competition": {"id": "99", "name": "Premier League"},
            "region": {"name": "England"},
            "startTime": "2026-04-15T15:00:00Z",
            "widgets": [{"id": "555", "type": "SPORTRADAR"}],
            "results": None,
        }
        event = parse_live_event(data)
        assert event is not None
        assert event.event_id == "bp:12345"
        assert event.home_team == "Team A"
        assert event.away_team == "Team B"

    def test_returns_none_for_missing_id(self):
        event = parse_live_event({})
        assert event is None


class TestParseUpcomingEvent:
    def test_parses_upcoming_event(self):
        data = {
            "id": "99001",
            "startTime": "2026-04-15T18:00:00Z",
            "participants": [
                {"position": 1, "name": "Home FC"},
                {"position": 2, "name": "Away United"},
            ],
            "competition": {"name": "Serie A"},
            "region": {"name": "Italy"},
            "widgets": [{"id": "777", "type": "SPORTRADAR"}],
        }
        event = parse_upcoming_event(data)
        assert event is not None
        assert event.event_id == "99001"
        assert event.home_team == "Home FC"
        assert event.start_time == datetime(2026, 4, 15, 18, 0, tzinfo=UTC)


class TestParseEventMarkets:
    def test_parses_markets(self):
        data = {
            "markets": [
                {
                    "marketType": {"id": "3743", "name": "1X2 - FT", "priority": 1},
                    "row": [
                        {
                            "id": "r1",
                            "handicap": None,
                            "prices": [
                                {"id": "p1", "name": "1", "typeId": "t1", "price": 2.0, "suspended": False},
                            ],
                        }
                    ],
                }
            ]
        }
        markets = parse_event_markets(data)
        assert len(markets) == 1
        assert markets[0].market_type_name == "1X2 - FT"
        assert markets[0].rows[0].selections[0].price == 2.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest tests/test_parsers.py -v`
Expected: FAIL — module `clients.parsers` does not exist.

- [ ] **Step 3: Create parsers.py by extracting from betpawa.py**

Create `src/live_coverage_bot/clients/parsers.py`:

```python
"""Parsers for BetPawa API response data."""

import logging
from datetime import UTC, datetime
from typing import Any

from live_coverage_bot.clients.models import (
    LiveEvent,
    ProviderID,
    ProviderType,
    UpcomingEvent,
)

logger = logging.getLogger(__name__)


def extract_provider_ids(widgets: list[dict[str, Any]]) -> list[ProviderID]:
    """Extract provider IDs from widgets array."""
    seen: set[tuple[str, str]] = set()
    provider_ids: list[ProviderID] = []

    for widget in widgets:
        widget_type = widget.get("type", "")
        widget_id = widget.get("id", "")

        if not widget_type or not widget_id:
            continue
        if widget_type not in ("SPORTRADAR", "GENIUSSPORTS"):
            continue

        key = (widget_type, widget_id)
        if key in seen:
            continue
        seen.add(key)

        try:
            provider_type = ProviderType(widget_type)
            provider_ids.append(ProviderID(type=provider_type, id=widget_id))
        except ValueError:
            logger.warning("Unknown provider type: %s", widget_type)

    return provider_ids


def extract_scores(
    results: dict[str, Any],
) -> tuple[int | None, int | None]:
    """Extract home and away FT scores from participantPeriodResults."""
    home_score: int | None = None
    away_score: int | None = None

    participant_results = results.get("participantPeriodResults", [])

    for pr in participant_results:
        participant = pr.get("participant", {})
        participant_type = participant.get("type")
        period_results = pr.get("periodResults", [])

        for period_result in period_results:
            period = period_result.get("period", {})
            if period.get("slug") == "FULL_TIME_EXCLUDING_OVERTIME":
                result_str = period_result.get("result", "")
                try:
                    score = int(result_str)
                    if participant_type == "HOME":
                        home_score = score
                    elif participant_type == "AWAY":
                        away_score = score
                except ValueError:
                    logger.warning("Invalid score value: %r", result_str)
                break

    return home_score, away_score


def parse_live_event(event_data: dict[str, Any]) -> LiveEvent | None:
    """Parse a single live event from BetPawa API response."""
    event_id = event_data.get("id", "")
    if not event_id:
        return None

    event_id = f"bp:{event_id}"

    participants = event_data.get("participants", [])
    home_team = ""
    away_team = ""
    for participant in participants:
        position = participant.get("position")
        name = participant.get("name", "")
        if position == 1:
            home_team = name
        elif position == 2:
            away_team = name

    competition = event_data.get("competition") or {}
    competition_id = str(competition.get("id", ""))
    competition_name = competition.get("name", "")

    region = event_data.get("region") or {}
    country_name = region.get("name") or None

    results = event_data.get("results") or {}
    display = results.get("display") or {}
    minute = display.get("minute")

    home_score, away_score = extract_scores(results)

    start_time_str = event_data.get("startTime", "")
    start_time = datetime.now(tz=UTC)
    if start_time_str:
        try:
            start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Invalid startTime: %r, using current time", start_time_str)

    provider_ids = extract_provider_ids(event_data.get("widgets", []))

    event = LiveEvent(
        event_id=event_id,
        home_team=home_team,
        away_team=away_team,
        competition_id=competition_id,
        competition_name=competition_name,
        country_name=country_name,
        minute=minute,
        home_score=home_score,
        away_score=away_score,
        start_time=start_time,
    )
    if provider_ids:
        event._provider_ids_override = provider_ids

    return event


def parse_upcoming_event(event_data: dict[str, Any]) -> UpcomingEvent | None:
    """Parse a single upcoming event from BetPawa API response."""
    event_id = event_data.get("id", "")
    if not event_id:
        return None

    start_time_str = event_data.get("startTime", "")
    if not start_time_str:
        return None

    try:
        start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Invalid startTime in upcoming event: %r", start_time_str)
        return None

    participants = event_data.get("participants", [])
    home_team = ""
    away_team = ""
    for participant in participants:
        position = participant.get("position")
        name = participant.get("name", "")
        if position == 1:
            home_team = name
        elif position == 2:
            away_team = name

    competition = event_data.get("competition") or {}
    competition_name = competition.get("name", "")
    region = event_data.get("region") or {}
    country_name = region.get("name") or None

    widgets = event_data.get("widgets", [])
    provider_ids = extract_provider_ids(widgets)

    return UpcomingEvent(
        event_id=str(event_id),
        home_team=home_team,
        away_team=away_team,
        competition_name=competition_name,
        country_name=country_name,
        start_time=start_time,
        provider_ids=provider_ids,
    )


def parse_event_markets(data: dict[str, Any]) -> list["Market"]:
    """Parse all markets from a BetPawa event detail response."""
    from live_coverage_bot.models.markets import Market, MarketRow, Selection

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
```

- [ ] **Step 4: Update BetPawaClient to use parsers**

In `src/live_coverage_bot/clients/betpawa.py`:

1. Add import at top:
```python
from live_coverage_bot.clients.parsers import (
    extract_provider_ids,
    parse_event_markets,
    parse_live_event,
    parse_upcoming_event,
)
```

2. In `get_upcoming_events()`, replace the inline parsing block (lines 121-178) for each event with:
```python
for event_data in event_list:
    parsed = parse_upcoming_event(event_data)
    if parsed is None:
        continue

    if parsed.start_time > cutoff_time:
        cutoff_crossed = True
        break

    events.append(parsed)
    page_kept += 1
```

3. Replace `_parse_events` / `_parse_single_event` methods with:
```python
def _parse_events(self, data: dict[str, Any]) -> list[LiveEvent]:
    """Parse API response into LiveEvent models."""
    events: list[LiveEvent] = []
    responses = data.get("responses", [])
    if not responses:
        return events

    event_list = responses[0].get("responses", [])
    for event_data in event_list:
        try:
            event = parse_live_event(event_data)
            if event:
                events.append(event)
        except Exception as e:
            logger.warning(
                "Failed to parse event %s: %s",
                event_data.get("id", "unknown"),
                e,
            )
    return events
```

4. Delete the old `_parse_single_event`, `_extract_scores`, and `_extract_provider_ids` methods entirely.

5. Replace the inline market parsing in `get_event_markets()` with:
```python
async def get_event_markets(self, event_id: str) -> list["Market"]:
    """Fetch full event detail including all markets."""
    try:
        response = await self._client.get(f"/events/{event_id}")
        response.raise_for_status()
        data = response.json()
        return parse_event_markets(data)
    except httpx.HTTPStatusError as e:
        logger.error("BetPawa event detail API returned error: %s", e.response.status_code)
        raise BetPawaError(f"Event detail API returned status {e.response.status_code}") from e
    except httpx.RequestError as e:
        logger.error("BetPawa event detail API request failed: %s", e)
        raise BetPawaError(f"Event detail request failed: {e}") from e
```

6. Remove now-unused imports (`from live_coverage_bot.models.markets import Market, MarketRow, Selection` from the function-level import in `get_event_markets` is no longer needed — it's in parsers now).

- [ ] **Step 5: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/clients/parsers.py src/live_coverage_bot/clients/betpawa.py tests/test_parsers.py
git commit -m "refactor: extract BetPawa response parsers into clients/parsers.py"
```

---

### Task 5: Fix Exception Handling

Replace broad catches and silent passes with specific exception types and logging.

**Files:**
- Modify: `src/live_coverage_bot/clients/betpawa.py`
- Modify: `src/live_coverage_bot/core/loop.py`

- [ ] **Step 1: Remove catch-all in betpawa.py get_upcoming_events**

In `src/live_coverage_bot/clients/betpawa.py`, the `get_upcoming_events()` method already catches `HTTPStatusError` and `RequestError` specifically. Remove the trailing `except Exception as e:` block (around line 202). The specific catches handle all expected failures; unexpected exceptions should propagate.

- [ ] **Step 2: Remove catch-all in betpawa.py get_live_events**

Same pattern — remove the trailing `except Exception as e:` block (around line 268). Keep the `HTTPStatusError` and `RequestError` catches.

- [ ] **Step 3: Narrow loop.py poll cycle catch-all**

In `src/live_coverage_bot/core/loop.py`, the main loop (line 66) has `except Exception`. Replace with:

```python
except (BetPawaError, SlackError) as e:
    logger.warning("Error in poll cycle: %s", e)
except Exception:
    logger.exception("Unexpected error in poll cycle")
```

This separates expected operational failures from bugs.

- [ ] **Step 4: Move late import to module level**

In `src/live_coverage_bot/core/loop.py`, the import `from live_coverage_bot.core.market_reporter import MarketReporter` at line 285 inside `_check_weekly_report`. Move it to the top of the file with the other imports.

- [ ] **Step 5: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/clients/betpawa.py src/live_coverage_bot/core/loop.py
git commit -m "refactor: narrow exception handling and move late import to module level"
```

---

### Task 6: Extract Magic Numbers and Standardize JSON

**Files:**
- Modify: `src/live_coverage_bot/config/models.py`
- Modify: `src/live_coverage_bot/core/reporter.py`
- Modify: `src/live_coverage_bot/clients/betpawa.py`
- Modify: `src/live_coverage_bot/db/market_repository.py`
- Modify: `src/live_coverage_bot/models/markets.py`
- Modify: `src/live_coverage_bot/models/events.py`

- [ ] **Step 1: Add on_time_threshold_seconds to config**

In `src/live_coverage_bot/config/models.py`, add to `ThresholdConfig`:

```python
class ThresholdConfig(BaseModel):
    """Event lifecycle threshold configuration."""

    grace_period_minutes: int = 5
    hard_timeout_minutes: int = 90
    on_time_threshold_seconds: int = 300
```

- [ ] **Step 2: Use config value in reporter.py**

In `src/live_coverage_bot/core/reporter.py`:

1. Update `__init__` to accept the threshold:
```python
def __init__(
    self, repo: EventRepository, week_starts: str = "tuesday",
    on_time_threshold_seconds: int = 300,
) -> None:
    self._repo = repo
    self._week_starts = week_starts.lower()
    self._on_time_threshold = on_time_threshold_seconds
```

2. Replace all three hardcoded `300` values (lines 63, 64, 174) with `self._on_time_threshold`.

3. Update callers in `loop.py` and `__main__.py` to pass the config value:
```python
reporter = WeeklyReporter(
    self._repo,
    week_starts=cfg.week_starts,
    on_time_threshold_seconds=self._settings.thresholds.on_time_threshold_seconds,
)
```

- [ ] **Step 3: Add API constants to betpawa.py**

At the top of `src/live_coverage_bot/clients/betpawa.py`, add module-level constants:

```python
DEFAULT_PAGE_SIZE = 100
FOOTBALL_CATEGORY_ID = "2"
EVENTS_ENDPOINT = "/events/lists/by-queries"
```

Replace hardcoded `100` with `DEFAULT_PAGE_SIZE`, `"2"` with `FOOTBALL_CATEGORY_ID`, and `"/events/lists/by-queries"` with `EVENTS_ENDPOINT` in both `get_upcoming_events` and `get_live_events`.

- [ ] **Step 4: Standardize JSON serialization**

Create a small constant. In `src/live_coverage_bot/models/markets.py`, at the top:
```python
JSON_SEPARATORS: tuple[str, str] = (",", ":")
```

Use it in `MarketSnapshot.markets_json`:
```python
return json.dumps([m.model_dump() for m in self.markets], separators=JSON_SEPARATORS)
```

In `src/live_coverage_bot/models/events.py`, update `provider_ids_json` to use the same:
```python
from live_coverage_bot.models.markets import JSON_SEPARATORS
# ...
return json.dumps(
    [{"type": pid.type.value, "id": pid.id} for pid in self.provider_ids],
    separators=JSON_SEPARATORS,
)
```

In `src/live_coverage_bot/db/market_repository.py`, import and use:
```python
from live_coverage_bot.models.markets import JSON_SEPARATORS
# In insert_comparison:
json.dumps(cmp.details.model_dump(), separators=JSON_SEPARATORS)
```

- [ ] **Step 5: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/config/models.py src/live_coverage_bot/core/reporter.py src/live_coverage_bot/clients/betpawa.py src/live_coverage_bot/db/market_repository.py src/live_coverage_bot/models/markets.py src/live_coverage_bot/models/events.py src/live_coverage_bot/core/loop.py src/live_coverage_bot/__main__.py
git commit -m "refactor: extract magic numbers to config and standardize JSON serialization"
```

---

### Task 7: Emoji Constants and Datetime Parse Helper

**Files:**
- Modify: `src/live_coverage_bot/clients/slack.py`
- Modify: `src/live_coverage_bot/db/repository.py`
- Modify: `src/live_coverage_bot/db/market_repository.py`

- [ ] **Step 1: Add emoji constants to slack.py**

At the top of `src/live_coverage_bot/clients/slack.py`, after imports:

```python
# Slack message emoji constants
EMOJI_LATE = "\U0001f7e1"        # 🟡
EMOJI_LIVE = "\U0001f7e2"        # 🟢
EMOJI_NEVER_LIVE = "\U0001f534"  # 🔴
EMOJI_CLIPBOARD = "\U0001f4cb"   # 📋
EMOJI_CLOCK = "\u23f0"           # ⏰
EMOJI_PLUG = "\U0001f50c"        # 🔌
EMOJI_ID = "\U0001f194"          # 🆔
EMOJI_WARNING = "\u26a0\ufe0f"   # ⚠️
EMOJI_CHECK = "\u2705"           # ✅
EMOJI_STOP = "\U0001f6d1"       # 🛑
EMOJI_CHART = "\U0001f4ca"      # 📊
EMOJI_DASH = "\u2014"           # —
```

Replace all inline emoji characters in `format_parent_message`, `format_thread_reply`, `format_market_recap`, and `format_market_anomaly_parent` with these constants. For example:

```python
# Before:
f"\U0001f7e1 LATE \u2014 {event.home_team} vs {event.away_team}\n"
# After:
f"{EMOJI_LATE} LATE {EMOJI_DASH} {event.home_team} vs {event.away_team}\n"
```

- [ ] **Step 2: Add datetime parse helper to repository.py**

In `src/live_coverage_bot/db/repository.py`, add a module-level helper:

```python
def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO datetime string, returning None if the value is None."""
    return datetime.fromisoformat(value) if value else None
```

Then simplify `_row_to_event`:

```python
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
        first_seen_live=_parse_dt(row["first_seen_live"]),
        transition_delay_sec=row["transition_delay_sec"],
        slack_message_ts=row["slack_message_ts"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )
```

- [ ] **Step 3: Run all tests**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m pytest -v`
Expected: All tests PASS.

- [ ] **Step 4: Run ruff linter**

Run: `cd c:/Users/loren/Desktop/betpawa/live_coverage_bot && python -m ruff check src/`
Expected: No errors (or only pre-existing ones unrelated to our changes).

- [ ] **Step 5: Commit**

```bash
cd c:/Users/loren/Desktop/betpawa/live_coverage_bot
git add src/live_coverage_bot/clients/slack.py src/live_coverage_bot/db/repository.py src/live_coverage_bot/db/market_repository.py
git commit -m "refactor: add emoji constants and datetime parse helper"
```
