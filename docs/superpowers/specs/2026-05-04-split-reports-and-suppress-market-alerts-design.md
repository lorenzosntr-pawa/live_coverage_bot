# Split Reports & Suppress Market Anomaly Alerts

**Date:** 2026-05-04
**Approach:** Config-driven alert suppression + split reporter output

## Context

The bot currently sends real-time Slack alerts for market anomalies (dropped markets, odds shifts) on top-league events. These alerts are noisy and not actionable in real-time. Market data should still be collected for weekly analysis, but real-time alerts should be suppressed.

The weekly report is currently a single combined Slack message covering both live coverage stats and market retention stats. This needs to be split into two independent reports, each with its own CSV attachment for analysis.

## Changes

### 1. Suppress real-time market anomaly alerts

**Config change:** Add `alerts_enabled: bool = True` to `MarketsConfig` in `config/models.py`. Set to `false` in `config.yaml`.

```yaml
markets:
  enabled: true           # data collection continues
  alerts_enabled: false   # real-time Slack alerts suppressed
```

**Gate location:** `_handle_market_comparison()` in `core/loop.py`. After the comparison is inserted into the database (line ~433), check `self._settings.markets.alerts_enabled`. If `false`, return early. This preserves all data collection (snapshots, comparisons) while silencing Slack posts.

Event lifecycle alerts (LATE, LIVE, NEVER_LIVE, REMOVED) are unaffected.

### 2. Split weekly report into two Slack messages

Currently `_check_weekly_report()` in `core/loop.py` builds one combined summary and calls `post_summary()` once. This becomes two independent report flows:

#### Message 1 — Live Coverage Report

**Slack message content** (unchanged from current lifecycle summary):
- Total prematch events tracked
- On-time / late / never-live / removed / unmonitored counts with percentages
- Avg and worst delay for late events
- Late reason breakdown (coverage late, match delayed, rescheduled)
- Per-provider statistics
- Top 5 competitions with most issues
- Top leagues block (per-league: total events, on-time %, late count, never-live count, avg delay) — **without** market retention % per league

**CSV attachment:** `reports/weekly-YYYY-MM-DD.csv` uploaded to Slack as a file, threaded to the coverage message. Contains all events (including on-time):

```
date, betpawa_event_id, home_team, away_team, competition, country,
provider_type, provider_id, scheduled_kickoff, status, first_seen_live,
transition_delay_sec, late_reason, live_minute
```

#### Message 2 — Market Retention Report

**Slack message content** (minimal — 3 headline numbers):
- Events with market snapshots: N
- Avg market retention: X%
- Events with significant drops (retention below configured `retention_below_pct` threshold): N

**CSV attachment:** `reports/weekly-markets-YYYY-MM-DD.csv` uploaded to Slack as a file, threaded to the market retention message. Contains all events with market snapshots:

```
betpawa_event_id, home, away, competition, kickoff, prematch_markets,
live_markets, retention_pct, dropped_key_markets, max_odds_shift_pct,
triggered_alert
```

**Per-event market CSVs** (`reports/events/YYYY-MM-DD/`) continue to be written to disk only.

Both messages posted at the same scheduled time (default: Tuesday 08:00 UTC), to `summary_channel_id` (or fallback to `channel_id`).

### 3. Slack file upload

New `upload_file()` method on `SlackClient` using Slack's `files.uploadV2` API. Supports:
- File content (CSV string)
- Filename
- Channel
- `thread_ts` for threading to a parent message

## Files changed

| File | Change |
|------|--------|
| `config/models.py` | Add `alerts_enabled: bool = True` to `MarketsConfig` |
| `config.yaml` | Add `alerts_enabled: false` under `markets:` |
| `core/loop.py` | Gate in `_handle_market_comparison()` + split `_check_weekly_report()` into two report flows with file uploads |
| `core/market_reporter.py` | New `generate_minimal_summary()` method; remove retention % from `generate_top_leagues_block()` |
| `clients/slack.py` | New `upload_file()` method via `files.uploadV2` |
| Tests | Update split report flow, new tests for alert gate and minimal summary |

## Files NOT changed

- `core/reporter.py` — summary and CSV generation unchanged
- `core/market_comparator.py` — comparison logic unchanged
- `core/market_snapshotter.py` — snapshot scheduling unchanged
- `core/tracker.py` — lifecycle tracking unchanged
- `db/` — database layer unchanged
- `models/` — domain models unchanged
