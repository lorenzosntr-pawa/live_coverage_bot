# BetPawa Market Comparison (Prematch vs Live) — Design Spec

**Date:** 2026-04-16
**Status:** Approved
**Approach:** Inline in existing monitoring loop (Approach A)
**Depends on:** The v2 prematch-to-live monitor (already implemented)

## Overview

Adds market-level comparison on top of the existing prematch-to-live event lifecycle monitor. For each tracked event, takes multiple prematch market snapshots (60/15/1 minute before kickoff) and one live snapshot (on transition to LIVE). Compares the snapshots to surface:

1. **Count** — how many markets dropped/added between prematch and live
2. **Identity** — which specific market types disappeared (especially "key markets")
3. **Odds** — how prices shifted on markets that survived

Outputs:

- **Slack thread recap** for events that had lifecycle alerts (appended to existing thread)
- **Standalone Slack alert** for happy-path events when the comparison is noteworthy (configurable thresholds)
- **Weekly aggregate report** (extended Slack summary + new market summary CSV)
- **Per-event CSV** with full market timeline for deep analysis

## Event Detail Client & Market Data Model

New endpoint added to `BetPawaClient`:

```python
async def get_event_markets(self, event_id: str) -> list[Market]:
    """GET /events/{event_id} — full event data with markets[] array."""
```

### Domain models (`models/markets.py`)

```python
class SnapshotPhase(StrEnum):
    PREMATCH_60 = "PREMATCH_60"
    PREMATCH_15 = "PREMATCH_15"
    PREMATCH_1 = "PREMATCH_1"
    LIVE = "LIVE"

class Selection(BaseModel):
    price_id: str            # price.id (instance-level)
    name: str                # "1", "X", "2", "Over", etc.
    type_id: str             # typeId — stable across phases (e.g., "3744" = home win)
    price: float             # odds
    suspended: bool

class MarketRow(BaseModel):
    row_id: str              # row.id
    handicap: str | None     # null for 1X2, "2.5" for O/U, "-1.5" for AH
    selections: list[Selection]

class Market(BaseModel):
    market_type_id: str      # stable across prematch and live
    market_type_name: str    # "1X2 - FT", "Both Teams To Score - FT", etc.
    priority: int            # from API — for ranking importance
    rows: list[MarketRow]

class MarketSnapshot(BaseModel):
    id: int | None = None
    event_id: int            # FK to TrackedEvent.id
    phase: SnapshotPhase
    taken_at: datetime
    markets: list[Market]
    total_market_count: int
    total_selection_count: int
    suspended_count: int

class MarketComparison(BaseModel):
    id: int | None = None
    event_id: int
    compared_at: datetime
    prematch_phase: SnapshotPhase   # which prematch phase we compared against
    markets_added: int
    markets_dropped: int
    markets_kept: int
    retention_pct: float
    dropped_key_markets: list[str]
    max_odds_shift_pct: float
    triggered_alert: bool
    details: dict                    # serialized as details_json in DB
```

### Storage strategy

Store the full snapshot as JSON blob (`markets_json` column) plus denormalized count fields. Rationale:
- Per-event CSV export needs full detail — JSON blob covers it
- Aggregate weekly report needs counts/percentages — fast via denormalized columns
- Avoids expensive JOINs and JSON parsing during weekly report generation

## Snapshot Scheduling & Windows

### Snapshot phases

| Phase | Trigger condition |
|-------|-------------------|
| `PREMATCH_60` | Event is PREMATCH, current time is in `[kickoff - 60min, kickoff - 55min]` |
| `PREMATCH_15` | Event is PREMATCH, current time is in `[kickoff - 15min, kickoff - 10min]` |
| `PREMATCH_1` | Event is PREMATCH, current time is in `[kickoff - 1min, kickoff]` |
| `LIVE` | Event just transitioned from PREMATCH or LATE to LIVE (first cycle after transition) |

Windows are 5 min wide for 60/15 and 1 min wide for the final snapshot — this tolerates missed cycles while keeping snapshot timing predictable. Each phase is taken at most once per event.

### Per-cycle integration

Added to existing polling loop (after `check_transitions`, before `detect_removed`):

```
For each active event (PREMATCH or LATE):
  For each phase not yet taken:
    If current time is in phase's window:
      → schedule snapshot for this event

For each event with new LIVE transition this cycle:
  → schedule LIVE snapshot

asyncio.gather all scheduled snapshots in parallel
For each completed snapshot:
  → store in DB
  → if LIVE phase: run comparison + alert decision
```

**Efficiency:** expected 0-2 snapshots per cycle normally, 20-30 during kickoff waves. Event detail calls ~200ms each, parallelized — well under 30s cycle budget.

**Graceful degradation:** If a snapshot fetch fails, log and skip. Don't mark the phase as taken — retry next cycle within the same window. Windows that close without a successful snapshot are logged and surfaced in the weekly report as "missing snapshots: N".

## Storage Schema

Two new SQLite tables in `data/events.db`:

### Table: `market_snapshots`

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| event_id | INTEGER FK | → `events.id` with `ON DELETE CASCADE` |
| phase | TEXT | `PREMATCH_60` / `PREMATCH_15` / `PREMATCH_1` / `LIVE` |
| taken_at | DATETIME | Snapshot timestamp (UTC) |
| markets_json | TEXT | Full `list[Market]` JSON |
| total_market_count | INTEGER | |
| total_selection_count | INTEGER | |
| suspended_count | INTEGER | |
| fetch_duration_ms | INTEGER | For API performance monitoring |

Unique constraint: `(event_id, phase)`.

Indexes: `event_id`, `taken_at`, `phase`.

### Table: `market_comparisons`

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| event_id | INTEGER FK | → `events.id` with `ON DELETE CASCADE` |
| compared_at | DATETIME | |
| prematch_phase | TEXT | Which prematch snapshot was used (usually `PREMATCH_1`) |
| markets_added | INTEGER | |
| markets_dropped | INTEGER | |
| markets_kept | INTEGER | |
| retention_pct | REAL | `kept / total_prematch * 100` |
| dropped_key_markets | TEXT (JSON) | List of key market names that disappeared |
| max_odds_shift_pct | REAL | Biggest % shift across all kept selections |
| triggered_alert | BOOLEAN | Whether this crossed alert threshold |
| details_json | TEXT | Full per-market diff (for thread recap, weekly report) |

Indexes: `event_id`, `compared_at`, `triggered_alert`.

Retention is handled by the existing cleanup (FK cascade deletes snapshots and comparisons when events are purged).

## Comparison Logic

Runs when a LIVE snapshot is stored. Compares against the most recent available prematch snapshot: prefers `PREMATCH_1`, falls back to `PREMATCH_15`, then `PREMATCH_60`.

If NO prematch snapshot exists (event discovered too late or all prematch fetches failed), no comparison is recorded — the LIVE snapshot is still stored for the per-event CSV, but no row is written to `market_comparisons` and no alert fires. This is logged at INFO level and surfaced in the weekly report as "events with LIVE snapshot only: N".

### Diff algorithm

- **Markets matched** by `market_type_id` (stable identifier across phases)
- **Rows within a market matched** by exact `handicap` field value (Over 2.5 ≠ Over 3.5 — they're distinct offerings)
- **Selections within a row matched** by `typeId` (e.g., `3744` = "1 (home)" across all 1X2 markets)
- **Suspended selections** excluded from odds shift calculations

### Metrics computed

```python
markets_dropped = |prematch market_type_ids − live market_type_ids|
markets_added   = |live market_type_ids − prematch market_type_ids|
markets_kept    = |prematch ∩ live|
retention_pct   = markets_kept / |prematch| × 100

dropped_key_markets = [name for name in prematch_dropped if name in config.key_markets]

for each kept market, matched row, matched non-suspended selection:
    shift_pct = |live.price - prematch.price| / prematch.price × 100
max_odds_shift_pct = max(shifts, default=0)
```

### Details blob

Full diff stored in `details_json` for rendering thread recaps and weekly reports:

```json
{
  "dropped": ["Corner markets", "Player specials", "..."],
  "added": [],
  "odds_shifts": [
    {"market": "1X2 - FT", "selection": "1", "handicap": null,
     "prematch_price": 2.10, "live_price": 2.45, "shift_pct": 16.7},
    ...
  ]
}
```

## Alerting

### Alert triggers (OR semantics — any one fires)

```yaml
market_comparison:
  alert_thresholds:
    retention_below_pct: 50        # < 50% retention
    any_key_market_dropped: true   # any key market disappeared
    max_odds_shift_pct: 30         # any selection shifted > 30%
```

### Key markets (configurable, defaults)

```yaml
  key_markets:
    - "1X2 - FT"
    - "Total Score Over/Under - FT"
    - "Both Teams To Score - FT"
    - "Double Chance - FT"
    - "1X2 - 1H"
```

### Slack routing

Three cases:

1. **Event had a LATE alert already** (existing `slack_message_ts` in DB): post market recap as a thread reply on that existing thread. Parent message is not edited.

2. **Event was happy-path (PREMATCH → LIVE on time) AND comparison is noteworthy**: post a new standalone Slack parent message announcing the market anomaly. Store new `slack_message_ts` so future updates can thread.

3. **Event was happy-path AND not noteworthy**: silent. Data goes to DB + weekly report + per-event CSV only.

### Thread recap format (case 1)

```
15:12 — 📊 Market comparison (prematch 1min → live)
 • 87 prematch markets → 34 live (39% retention)
 • Dropped: 53 markets including:
   - Corner markets (Over/Under)
   - Player specials
   - First goalscorer
 • ⚠️ Key market dropped: Both Teams To Score - FT
 • Biggest odds shift: 1X2 home 2.10 → 2.45 (+16.7%)
```

### Standalone alert format (case 2)

```
📊 MARKET ANOMALY — Team A vs Team B
📋 England Premier League | England
⏰ Kickoff: 15:00 | Went live on time
🔌 SPORTRADAR #12345

87 prematch markets → 34 live (39% retention)
⚠️ Key market dropped: Both Teams To Score
Biggest odds shift: 1X2 home +16.7%
```

## Reporting

### Weekly Slack summary — new section appended

```
─── Markets ───

Events with market snapshots: 287 (of 342 tracked)
Avg markets in prematch (1min before kickoff): 87.3
Avg markets in live (first snapshot): 34.1
Avg market retention: 39%
Events where ≥50% of markets dropped: 89 (31%)
Events where ≥80% of markets dropped: 14 (5%)

Market types with worst retention (≥20 events):
  1. Corner markets           — 12% retention
  2. Player specials          — 18% retention
  3. First/last goalscorer    — 22% retention
  4. Halftime/fulltime combo  — 34% retention
  5. Both teams to score - 2H — 45% retention

Key markets that disappeared when going live:
  1X2 - FT:          3 events
  Over/Under 2.5:    7 events
  BTTS:              12 events

Odds shifts (prematch 1min → live first snapshot):
  1X2 home:     avg ±8.2% (max 34%)
  1X2 draw:     avg ±5.1%
  Over 2.5:     avg ±6.7%

By provider:
  SPORTRADAR:   avg 42% retention (234 events)
  GENIUSSPORTS: avg 28% retention (53 events)

Competitions with worst market coverage:
  1. Nigeria NPFL            — 18% avg retention (12 events)
  2. Egyptian Premier League — 24% avg retention (8 events)
```

### Aggregate CSV (new)

Path: `reports/weekly-markets-YYYY-MM-DD.csv`

Columns: `betpawa_event_id, home, away, competition, kickoff, prematch_markets, live_markets, retention_pct, dropped_key_markets, max_odds_shift_pct, triggered_alert`

One row per tracked event with snapshots.

### Per-event CSV (new)

Path: `reports/events/YYYY-MM-DD/<event_id>_<home>_vs_<away>.csv`

Columns: `phase, taken_at, market_type_id, market_type_name, handicap, selection_name, selection_type_id, price, suspended`

One row per (phase × market × row × selection). Allows pivot/group in Excel to track odds evolution and market drops per event.

## Configuration

Extension of existing `config.yaml` — all new, all optional with sensible defaults:

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

Environment variable override follows existing pattern: `LCB_MARKETS__ENABLED=false`.

## Project Structure

```
src/live_coverage_bot/
├── clients/
│   └── betpawa.py               # +1 method: get_event_markets()
├── config/
│   └── models.py                # +1 config section: MarketsConfig
├── core/
│   ├── loop.py                  # +1 call per cycle: snapshotter.run()
│   ├── market_snapshotter.py    # NEW: decides when/what to snapshot, stores
│   ├── market_comparator.py     # NEW: diff logic + alert decision
│   └── market_reporter.py       # NEW: weekly aggregate + per-event CSV
├── db/
│   └── market_repository.py     # NEW: CRUD for snapshots & comparisons
└── models/
    └── markets.py               # NEW: Market, Selection, Snapshot, Comparison
```

### Unit responsibilities

| File | Responsibility |
|------|----------------|
| `markets.py` | Data models only |
| `market_repository.py` | SQLite CRUD for snapshots and comparisons |
| `market_snapshotter.py` | "Which events need a snapshot now?" → fetch → store |
| `market_comparator.py` | "What changed between these two snapshots?" → alert decision |
| `market_reporter.py` | "Generate aggregate summary and per-event CSVs for a date range" |
| `loop.py` (extended) | Invokes snapshotter every cycle, comparator on LIVE transitions |

No existing file grows significantly. All new files stay under ~300 lines each.

## Future (v3)

- Per-competition snapshot policies (e.g., more frequent snapshots for Premier League matches)
- Historical trending across weeks (retention trend for specific market types)
- Anomaly detection via statistical baselines (alert when an event deviates from its provider's average retention)
- Odds shift prediction (using past shifts to predict upcoming behavior)
