# Late Reason Classification & Kickoff Reschedule Detection

**Date:** 2026-04-24
**Status:** Approved

## Problem

Two gaps in the bot's handling of delayed/late events:

1. **No distinction between "match delayed" and "coverage late"**: When a LATE event goes live, the bot records the transition delay but doesn't check the provider-reported match minute. A match at minute 2 means the match itself started late (not our problem). A match at minute 23 means coverage started late or we spotted it late (operational issue to track).

2. **No detection of kickoff reschedules**: When a provider changes the kickoff time (e.g., by 1 hour) shortly after the original kickoff, the bot fires a LATE alert for a match that is still in prematch. The bot only checks the live feed for LATE events, missing the updated kickoff in the prematch feed.

## Three Scenarios

| Scenario | What happened | Provider minute | Late reason |
|---|---|---|---|
| Match delayed | Match started late for external reasons | 0-5' | `MATCH_DELAYED` |
| Coverage late | Match started on time, provider/trader was slow | 6+' | `COVERAGE_LATE` |
| Kickoff rescheduled | Provider formally changed kickoff time | N/A (still prematch) | `KICKOFF_RESCHEDULED` |

**Match delayed vs. Kickoff rescheduled:** In "match delayed", the match eventually starts without a formal time change. In "kickoff rescheduled", the prematch feed shows a new kickoff time and the match transitions smoothly at the new time.

## Design

### Data Model Changes

**New DB columns** on `events` table:
- `late_reason TEXT` — stores `MATCH_DELAYED`, `COVERAGE_LATE`, or `KICKOFF_RESCHEDULED`. Nullable; only set when event was in LATE status or classified at live transition.
- `live_minute INTEGER` — the provider-reported minute when we first see the event in the live feed. Nullable.

**New config field** in `ThresholdConfig`:
- `coverage_late_minute_threshold: int = 5` — minute boundary. At or below = `MATCH_DELAYED`, above = `COVERAGE_LATE`.

**New fields on `TrackedEvent` model:**
- `late_reason: str | None = None`
- `live_minute: int | None = None`

**New field on `TransitionResult`:**
- `live_minute: int | None = None` — carried through to the alert handler.

### Fix 1: Late Reason Classification

**Location:** `tracker.py:_evaluate_transition`

**Signature changes to `check_transitions` and `_evaluate_transition`:**
- Add `live_events_map: dict[str, LiveEvent] | None = None` parameter — maps betpawa_id (without `bp:` prefix) to `LiveEvent`.
- Add `prematch_events_map: dict[str, UpcomingEvent] | None = None` parameter — maps betpawa_event_id to `UpcomingEvent`.

**Logic for `LATE -> LIVE` transition (line 299):**
1. Look up `LiveEvent` from `live_events_map` using the event's betpawa_event_id.
2. Parse `LiveEvent.minute` as an integer (default `None` if missing/unparsable).
3. Classify:
   - `minute is not None and minute <= coverage_late_minute_threshold` -> `MATCH_DELAYED`
   - `minute is not None and minute > coverage_late_minute_threshold` -> `COVERAGE_LATE`
   - `minute is None` -> leave `late_reason` as `None`
4. Store `late_reason` and `live_minute` in DB via extended `update_live_fields`.
5. Include `live_minute` on `TransitionResult`.

**Also applies to `PREMATCH -> LIVE` transition (line 269):**
Same minute-checking logic. Here `late_reason` would only be `COVERAGE_LATE` if `minute > threshold`. This catches the edge case where the bot detects the event going live directly from prematch (within the grace period) but the provider minute shows coverage was already underway — meaning the provider started late but we caught it before the LATE alert fired. If `minute <= threshold`, no `late_reason` is set (normal on-time transition).

**Slack alert changes:**
- In the "WENT LIVE" parent message and thread reply, include the provider minute.
- If `COVERAGE_LATE`: add a warning line: `:warning: Provider at minute {X}' -- coverage started late`
- If `MATCH_DELAYED`: add an info line: `Match started late (minute {X}')`

### Fix 2: Kickoff Reschedule Detection

**Location:** `tracker.py:_evaluate_transition`, the `LATE and not is_in_live_feed` branch (line 315), **before** the hard timeout check.

**Data flow:**
1. The prematch feed (`list[UpcomingEvent]`) is already fetched in `loop.py` on prematch cycles (~every 150s).
2. A `prematch_events_map: dict[str, UpcomingEvent]` is built and passed through to `check_transitions` -> `_evaluate_transition`.
3. When the prematch feed is not fetched (non-prematch cycle), the map is `None` and reschedule detection is skipped.

**Logic in `_evaluate_transition` for `LATE and not is_in_live_feed`:**
1. Before checking hard timeout, check if the event is in `prematch_events_map`.
2. If found, compare `prematch_event.start_time` with `event.scheduled_kickoff`.
3. If the kickoff differs by more than 1 minute (tolerance for minor variations):
   - Update `scheduled_kickoff` in the DB to the new time.
   - Set `late_reason = KICKOFF_RESCHEDULED`.
   - Transition status back to `PREMATCH`.
   - Record state change: `LATE -> PREMATCH` with details like `"Kickoff rescheduled: 12:00 -> 13:00 UTC"`.
   - Return a `TransitionResult`.

**Slack handling in `loop.py:_handle_transition` -- new branch for `LATE -> PREMATCH`:**
1. Edit the original LATE parent message in-place using `update_message` -- reformat with rescheduled status showing old and new kickoff.
2. Post a thread reply: `:calendar: Kickoff rescheduled: 12:00 -> 13:00 UTC -- reverted to prematch monitoring`

**New Slack formatting:**
- `format_reschedule_message(event, old_kickoff, new_kickoff)` on `SlackClient` for the updated parent message.
- Shows something like: `:calendar: RESCHEDULED -- Home vs Away` with old and new kickoff times.

**Full event lifecycle for rescheduled match:**
1. Event tracked with kickoff 12:00.
2. 12:05 -> marked LATE, alert sent.
3. 12:06 -> prematch feed shows new kickoff 13:00.
4. Revert to PREMATCH, update `scheduled_kickoff` to 13:00, edit Slack alert, set `late_reason = KICKOFF_RESCHEDULED`.
5. 13:00 -> Event goes LIVE normally at minute 0-1.
6. `transition_delay_sec` calculated from the **new** kickoff -> ~0, smooth transition.

State changes recorded: `PREMATCH -> LATE` (12:05), `LATE -> PREMATCH` (12:06, reschedule), `PREMATCH -> LIVE` (13:00).

### DB Migration & Repository Changes

**Schema migration** in `connection.py`:
- Add `late_reason TEXT` column to `events` table.
- Add `live_minute INTEGER` column to `events` table.
- Use `ALTER TABLE` with existing migration pattern (check if column exists before adding).

**Repository changes** in `repository.py`:
- Extend `update_live_fields` to accept optional `late_reason: str | None` and `live_minute: int | None`.
- Add `update_scheduled_kickoff(event_id, new_kickoff, late_reason)` method.
- Update `_row_to_event` to read the new columns.

### Weekly Report Changes

- Include `late_reason` breakdown in the weekly summary (count of `MATCH_DELAYED` vs `COVERAGE_LATE` vs `KICKOFF_RESCHEDULED`).
- Add `late_reason` and `live_minute` columns to the CSV export.

## Files to Modify

| File | Changes |
|---|---|
| `config/models.py` | Add `coverage_late_minute_threshold` to `ThresholdConfig` |
| `config.yaml` | Add `coverage_late_minute_threshold: 5` |
| `models/events.py` | Add `late_reason`, `live_minute` to `TrackedEvent` and `TransitionResult` |
| `db/connection.py` | Migration: add `late_reason`, `live_minute` columns |
| `db/repository.py` | Extend `update_live_fields`, add `update_scheduled_kickoff`, update `_row_to_event` |
| `core/tracker.py` | Add live/prematch maps to signatures, classification logic in `_evaluate_transition` |
| `core/loop.py` | Build maps from feed data, pass to tracker, handle `LATE -> PREMATCH` transition |
| `clients/slack.py` | Add `format_reschedule_message`, update `format_parent_message` for minute/reason display |
| `core/reporter.py` | Late reason breakdown in summary, new CSV columns |

## Configuration

```yaml
thresholds:
  grace_period_minutes: 5
  hard_timeout_minutes: 90
  on_time_threshold_seconds: 300
  coverage_late_minute_threshold: 5   # NEW
```
