# BetPawa Prematch-to-Live Monitor — Design Spec

**Date:** 2026-04-15
**Status:** Approved
**Approach:** Poll-and-Diff Loop (Approach A)

## Overview

Radical redesign of the live coverage bot. Removes SportyBet entirely. The bot becomes a BetPawa-only prematch-to-live event monitor that:

1. Tracks all BetPawa prematch football events through their lifecycle
2. Detects events that fail to go live or go live late
3. Measures the delay between scheduled kickoff and actual live availability
4. Surfaces provider info (SportRadar / GeniusSports) for manual pattern analysis
5. Alerts only on problems (late / never live) via Slack with threaded updates
6. Produces weekly summary reports (Slack + local CSV)

## Event Lifecycle & State Machine

Each prematch event is tracked through these states:

```
PREMATCH → LIVE        (happy path, no alert)
PREMATCH → LATE        (kickoff + grace_period passed, not in live feed → alert)
LATE     → LIVE        (appeared late → edit parent + thread reply)
LATE     → NEVER_LIVE  (hard timeout passed → edit parent + thread reply)
PREMATCH → REMOVED     (disappeared from prematch feed before kickoff → no alert, logged)
```

### Data tracked per event

| Field | Description |
|-------|-------------|
| betpawa_event_id | BetPawa's event ID (unique) |
| home_team / away_team | Team names |
| competition | League/tournament name |
| country | Country |
| scheduled_kickoff | From prematch data (UTC) |
| status | PREMATCH / LIVE / LATE / NEVER_LIVE / REMOVED |
| provider_ids | JSON array, e.g., `[{"type":"SPORTRADAR","id":"12345"}]` |
| first_seen_prematch | When we first discovered it |
| first_seen_live | When it appeared in live feed (nullable) |
| transition_delay_sec | Seconds between kickoff and live appearance (nullable) |
| slack_message_ts | Slack parent message ID for edits (nullable) |

### Configurable thresholds

- `grace_period_minutes` (default: 5) — how long after kickoff before marking LATE and alerting
- `hard_timeout_minutes` (default: 90) — how long after kickoff before marking NEVER_LIVE

### Alert rules

- **PREMATCH → LATE:** Send first Slack alert (new parent message)
- **LATE → LIVE:** Edit parent message, post thread reply with delay info
- **LATE → NEVER_LIVE:** Edit parent message, post thread reply with timeout info
- **PREMATCH → LIVE:** No alert (happy path)
- **PREMATCH → REMOVED:** No alert, logged in DB for weekly report

## Polling Loop & Data Flow

Single async loop with two polling frequencies:

- **Live events:** Every 30 seconds
- **Prematch events:** Every 2.5 minutes (150 seconds)

### Per-cycle logic

```
Every 30s:
1. Fetch BetPawa live events (every cycle)
2. Fetch BetPawa prematch events (every 5th cycle, ~2.5min)
   (parallel requests when both run)

3. Diff against SQLite:

   New prematch event?
   → INSERT as PREMATCH

   PREMATCH event now in live feed?
   → UPDATE to LIVE, record timestamp, calculate delay

   PREMATCH event past grace period?
   → UPDATE to LATE, send first Slack alert

   LATE event now in live feed?
   → UPDATE to LIVE, edit parent + thread reply

   LATE event past hard timeout?
   → UPDATE to NEVER_LIVE, edit parent + thread reply

   Prematch event disappeared from feed before kickoff?
   → UPDATE to REMOVED, log it

4. Log cycle summary (tracking X prematch, Y live, Z late)
```

### Matching prematch → live

By provider IDs — same proven logic from the current bot. An event is considered "live" if any of its provider IDs appear in the live feed.

### Prematch scope

Events starting within the next 3 hours. Configurable via `prematch_lookahead_hours`.

### Graceful degradation

If BetPawa API fails, skip the cycle and log. Do not change any event states on a failed fetch.

## SQLite Schema

**Database file:** `data/events.db` (configurable path)

### Table: `events`

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| betpawa_event_id | TEXT UNIQUE | BetPawa's event ID |
| home_team | TEXT | |
| away_team | TEXT | |
| competition | TEXT | |
| country | TEXT | |
| scheduled_kickoff | DATETIME | UTC |
| status | TEXT | PREMATCH / LIVE / LATE / NEVER_LIVE / REMOVED |
| provider_ids | TEXT (JSON) | Provider ID array |
| first_seen_prematch | DATETIME | First discovery time |
| first_seen_live | DATETIME | Live appearance time (nullable) |
| transition_delay_sec | INTEGER | Kickoff-to-live delay in seconds (nullable) |
| slack_message_ts | TEXT | Slack parent message ID (nullable) |
| created_at | DATETIME | Row creation |
| updated_at | DATETIME | Last state change |

### Table: `event_state_changes`

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| event_id | INTEGER FK | References events.id |
| old_status | TEXT | Previous state |
| new_status | TEXT | New state |
| changed_at | DATETIME | Transition time |
| details | TEXT | Context (e.g., "7min past kickoff") |

### Indexes

- `status` — querying active events per cycle
- `scheduled_kickoff` — timeout checks and report date ranges
- `betpawa_event_id` — unique, fast lookups during diff

### Cleanup

Events older than 30 days purged automatically. Configurable via `data_retention_days`.

## Slack Client & Alerting

### Auth

Slack Web API with Bot Token (`xoxb-...`). Required scopes: `chat:write`, `chat:update`.

Replaces the current webhook-based client entirely.

### Operations

1. **Post alert** (PREMATCH → LATE): `chat.postMessage` → returns `ts`, stored in SQLite
2. **Update parent** (any state change): `chat.update` using stored `ts`
3. **Post thread reply** (any state change): `chat.postMessage` with `thread_ts`

### Message format — Parent (edited on each state change)

**LATE:**
```
🟡 LATE — Team A vs Team B
📋 England Premier League | England
⏰ Kickoff: 15:00 UTC | Now: 12min late
🔌 SPORTRADAR #12345
```

**WENT LIVE (late):**
```
🟢 WENT LIVE — Team A vs Team B
📋 England Premier League | England
⏰ Kickoff: 15:00 UTC | Live at: 15:12 UTC (+12min)
🔌 SPORTRADAR #12345
```

**NEVER LIVE:**
```
🔴 NEVER LIVE — Team A vs Team B
📋 England Premier League | England
⏰ Kickoff: 15:00 UTC | Timed out after 90min
🔌 SPORTRADAR #12345
```

### Thread replies (chronological log)

```
15:05 — ⚠️ Not live yet (5min past kickoff)
15:12 — ✅ Went live (delay: 12min)
```

### Rate limiting

Slack API allows ~1 request/sec. Process state changes sequentially with small delays between API calls when multiple events change in the same cycle.

## Weekly Report System

### Trigger

Every Tuesday at 08:00 UTC. Configurable day/time. Also available via CLI flag (`--generate-report`). Covers the previous Tuesday–Monday period (aligned with football calendar — full weekend Friday–Monday is captured in one report).

### Slack summary (posted to channel)

```
📊 Weekly Report — Apr 7–13, 2026 (Tue–Mon)

Total prematch events tracked: 342
✅ Went live on time: 310 (90.6%)
🟡 Went live late: 18 (5.3%)
🔴 Never went live: 8 (2.3%)
🗑️ Removed before kickoff: 6 (1.8%)

Avg delay (late events): 8.2 min
Worst delay: 34 min — Team X vs Team Y (Apr 9)

By provider:
  SPORTRADAR: 280 events, 95% on time, avg delay 6.1min
  GENIUSSPORTS: 62 events, 82% on time, avg delay 12.4min

Top 5 competitions with most issues:
  1. Nigeria NPFL — 4 late, 2 never live
  2. Kenya Premier League — 3 late, 1 never live
  ...
```

### Local detailed file

Path: `reports/weekly-YYYY-MM-DD.csv`

Columns: `date, betpawa_event_id, home_team, away_team, competition, country, provider_type, provider_id, scheduled_kickoff, status, first_seen_live, transition_delay_sec`

One row per tracked event for the week. Importable into Excel/Google Sheets.

## Configuration

```yaml
polling:
  live_interval_seconds: 30
  prematch_interval_seconds: 150
  prematch_lookahead_hours: 3

thresholds:
  grace_period_minutes: 5
  hard_timeout_minutes: 90

betpawa:
  base_url: "https://www.betpawa.ng/api/sportsbook/v3"
  brand: "betpawa-nigeria"
  language: "en"

slack:
  bot_token: ""
  channel_id: ""
  summary_channel_id: ""

database:
  path: "data/events.db"
  retention_days: 30

reporting:
  day: "tuesday"
  time: "08:00"
  week_starts: "tuesday"  # Tue-Mon football week
  output_dir: "reports/"
```

Environment variable override: `LCB_` prefix (e.g., `LCB_SLACK__BOT_TOKEN`).

## Project Structure

```
src/live_coverage_bot/
├── __main__.py              # Entry point
├── clients/
│   ├── betpawa.py           # BetPawa API client (keep & adapt)
│   └── slack.py             # Slack Web API client (rewrite)
├── config/
│   ├── models.py            # Pydantic settings (update)
│   └── loader.py            # YAML + env loader (keep)
├── core/
│   ├── loop.py              # Main polling loop (rewrite)
│   ├── tracker.py           # Event lifecycle state machine (rewrite)
│   └── reporter.py          # Weekly report generator (new)
├── db/
│   ├── database.py          # SQLite connection & init (new)
│   ├── models.py            # DB schemas & queries (new)
│   └── migrations.py        # Schema creation (new)
└── models/
    └── events.py            # Event, ProviderID, EventStatus (refactored)
```

### Removed (SportyBet clean break)

- `clients/sportybet.py`
- `core/matcher.py`
- `core/prematch_cache.py`
- `core/formatter.py`

### Kept & adapted

- `clients/betpawa.py` — same API, same parsing, minor adjustments
- `config/loader.py` — same pattern
- Domain models — ProviderID stays, event models adapt

## Future (v2)

Market offering comparison between prematch and live for the same events — tracking which markets are available before kickoff vs after going live. Not in scope for this version but the data model and tracking infrastructure are designed to support it.
