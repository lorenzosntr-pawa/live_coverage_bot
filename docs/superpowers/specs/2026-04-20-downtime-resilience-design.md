# Downtime Resilience: Heartbeat, Recovery & Notifications

**Date:** 2026-04-20
**Status:** Approved
**Problem:** When the bot is stopped (manually or via crash) and later restarted, it evaluates all stale active events against the current live feed in a single cycle. Events whose kickoff passed during downtime immediately trigger LATE/NEVER_LIVE transitions, flooding Slack with dozens of alerts that have no value — the bot wasn't watching those matches.

## Goals

1. Detect that the bot was down on startup and for how long
2. Mark stale events as `UNMONITORED` instead of spamming individual LATE/NEVER_LIVE alerts
3. Notify the Slack channel when the bot goes down (graceful or crash)
4. Post a single recovery summary on startup after downtime
5. Keep weekly reports accurate by separating `UNMONITORED` from `NEVER_LIVE`

## Design

### 1. New `UNMONITORED` Status

- Add `UNMONITORED` to the `EventStatus` enum in `models/events.py`
- Mark as terminal (`is_terminal` returns `True`)
- `get_active_events()` already only fetches PREMATCH and LATE, so UNMONITORED events are automatically excluded from future poll cycles
- State change records are created with details like "Bot was offline from Sat 14:32 to Mon 09:15 UTC"
- No individual Slack alerts are fired for these transitions

### 2. Heartbeat Mechanism

**New `bot_state` table** in SQLite (single row):
- `last_heartbeat` — ISO timestamp, updated every poll cycle
- `started_at` — ISO timestamp, set on startup

Schema created via `CREATE TABLE IF NOT EXISTS` in `connection.py`.

**Every poll cycle:** After existing work in `_poll_cycle`, write `now` to `last_heartbeat`. One UPDATE statement, negligible cost.

**Startup detection** (in `MonitoringLoop.run()`, before entering the main loop):
1. Read `last_heartbeat` from `bot_state`
2. If exists and `now - last_heartbeat > 2 * live_interval_seconds` (default 60s): bot was down
3. Gap = `last_heartbeat` to `now`
4. If no row exists (first run): skip recovery
5. Write new `started_at` and `last_heartbeat` for this session

### 3. Startup Recovery — Marking Stale Events

**New method** `EventLifecycleTracker.mark_unmonitored(downtime_start, downtime_end, now)`:

Called once on startup after detecting a heartbeat gap, before the main loop.

**Logic:**
1. Fetch all active events (PREMATCH + LATE)
2. For each event: if `scheduled_kickoff` is in the past at time of restart → transition to `UNMONITORED`, record state change
3. If kickoff is in the future → leave alone, normal loop handles it
4. Return count of events marked

**Rule:** Any active event whose kickoff is in the past at restart gets marked UNMONITORED. We don't guess what happened — we acknowledge we weren't watching.

**Edge cases:**
- Event was LATE before shutdown, kickoff before downtime window: mark UNMONITORED
- Event kickoff still in future: leave as PREMATCH/LATE
- No active events during gap: recovery runs, marks zero, summary still posts noting downtime

### 4. Slack Notifications

All messages go to the existing configured `channel_id`. No new channel needed.

#### 4a. Graceful Shutdown
- **Trigger:** KeyboardInterrupt or SIGTERM caught in `__main__.py`
- **Message:** `"Bot shutting down (manual stop). Last cycle: {now} UTC"`
- Posted before process exits
- If Slack post fails: log error and exit anyway

#### 4b. Crash Notification
- **Trigger:** Unhandled exception escapes the main `while True` loop
- Current inner try/except catches per-cycle errors (BetPawaError, SlackError, Exception) and continues
- **Change:** Wrap outer `while True` in try/except that catches Exception, posts `"Bot crashed: {error_type}: {message}"` to Slack, then re-raises
- If Slack itself is the problem: log and crash without notification

#### 4c. Startup Recovery Summary
- **Trigger:** Bot starts and detects heartbeat gap
- **Message:**
  ```
  Bot restarting after downtime.
  Offline: Sat Apr 18 14:32 → Mon Apr 20 09:15 UTC (~18h 43min)
  12 events marked as unmonitored (kickoff occurred during downtime)
  3 active events carried over for normal monitoring
  ```
- Posted once, before main loop begins

### 5. Weekly Reports

**`generate_slack_summary()` in `reporter.py`:**
- New line: `"Unmonitored (bot offline): 12 (8.3%)"`
- UNMONITORED events excluded from:
  - "Problem events" section (top competitions with issues) — not BetPawa's fault
  - Delay stats (avg/worst delay calculations)
  - Provider stats — can't attribute blame if we weren't watching
- UNMONITORED events included in:
  - `Total prematch events tracked` count (they were registered)
  - CSV export as a status value (data available for filtering)

### 6. Non-Goals

- No external health checks (HTTP endpoint, PID file, watchdog)
- No Slack pinned status message
- No automatic restart (infra concern: systemd, Docker, etc.)
- No retroactive analysis (no querying BetPawa API for what happened during downtime)
- No config toggle — downtime protection is always on

## Files to Modify

| File | Change |
|------|--------|
| `models/events.py` | Add `UNMONITORED` to `EventStatus`, update `is_terminal` |
| `db/connection.py` | Add `bot_state` table to schema |
| `db/repository.py` | Add heartbeat read/write methods |
| `core/tracker.py` | Add `mark_unmonitored()` method |
| `core/loop.py` | Heartbeat writes, startup recovery logic, crash notification |
| `__main__.py` | Graceful shutdown Slack notification |
| `core/reporter.py` | Handle UNMONITORED in summary and stats |
| `clients/slack.py` | Add bot status message formatting methods |
