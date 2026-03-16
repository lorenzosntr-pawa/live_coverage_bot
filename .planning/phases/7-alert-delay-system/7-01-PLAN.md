---
phase: 7-alert-delay-system
plan: 01
type: execute
---

<objective>
Add configurable alert delay with top competition bypass to reduce false positive alerts.

Purpose: Events must be missing for X consecutive checks before alerting (except top competitions which alert immediately). Reduces noise from temporary sync glitches between SportyBet and BetPawa.

Output: AlertTracker with confirmation tracking, Settings with new config options, MonitoringLoop with delay logic.
</objective>

<execution_context>
~/.claude/get-shit-done/workflows/execute-phase.md
./summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/7-alert-delay-system/7-CONTEXT.md
@.planning/phases/06-monitoring-loop/06-01-SUMMARY.md

**Key files:**
@src/live_coverage_bot/config/models.py
@src/live_coverage_bot/core/tracker.py
@src/live_coverage_bot/core/loop.py

**Established patterns:**
- Pydantic settings with env var support (LCB_ prefix)
- In-memory tracking (no TTL, events expire naturally)
- AlertTracker for deduplication

**Constraining decisions:**
- Phase 6: In-memory set for alerted events, no persistence needed
- Phase 6.4: Pre-match cache filtering before alerts
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add alert delay configuration options</name>
  <files>src/live_coverage_bot/config/models.py</files>
  <action>
Add two new fields to the Settings class:

1. `alert_confirmation_checks: int = 10` - Number of consecutive checks an event must be missing before alerting (10 checks × 30s = 5 minutes)

2. `top_competitions: list[str]` - Competition names that bypass delay (alert on first detection). Default list:
   - "England Premier League"
   - "Spain LaLiga"
   - "France Ligue 1"
   - "Germany Bundesliga"
   - "Italy Serie A"
   - "UEFA Champions League"
   - "UEFA Europa League"
   - "UEFA Conference League"

These use the existing env var pattern: LCB_ALERT_CONFIRMATION_CHECKS=5, LCB_TOP_COMPETITIONS='["England Premier League"]'
  </action>
  <verify>python -c "from live_coverage_bot.config import Settings; s = Settings(slack={'webhook_url': 'https://example.com'}); print(f'checks={s.alert_confirmation_checks}, top_count={len(s.top_competitions)}')" shows checks=10 and top_count=8</verify>
  <done>Settings class has both new fields with correct defaults</done>
</task>

<task type="auto">
  <name>Task 2: Enhance AlertTracker with confirmation tracking</name>
  <files>src/live_coverage_bot/core/tracker.py</files>
  <action>
Extend AlertTracker to track consecutive missing counts:

1. Add `_missing_counts: dict[str, int]` to __init__

2. Add `record_missing(event_id: str) -> int` method:
   - Increment count for event_id (or set to 1 if new)
   - Return the new count

3. Add `clear_not_missing(still_missing_ids: set[str]) -> None` method:
   - Remove any event_id from _missing_counts that is NOT in still_missing_ids
   - This resets tracking for events that reappeared

4. Keep existing methods unchanged:
   - has_been_alerted() - checks if already alerted
   - mark_alerted() - prevents future alerts
   - clear(), get_alerted_count() - existing utility methods
  </action>
  <verify>python -c "from live_coverage_bot.core.tracker import AlertTracker; t = AlertTracker(); print(t.record_missing('e1'), t.record_missing('e1'), t.record_missing('e2')); t.clear_not_missing({'e1'}); print(t.record_missing('e2'))" shows "1 2 1" then "1" (e2 was reset)</verify>
  <done>AlertTracker tracks consecutive missing counts and can reset them</done>
</task>

<task type="auto">
  <name>Task 3: Integrate delay logic into MonitoringLoop</name>
  <files>src/live_coverage_bot/core/loop.py</files>
  <action>
Modify _poll_cycle() to use confirmation threshold:

1. After finding missing_events, collect their event_ids into a set for clear_not_missing()

2. For each missing event (after existing filters):
   - If already alerted → skip (existing)
   - If event.competition in settings.top_competitions → alert immediately, mark_alerted
   - Else:
     - count = tracker.record_missing(event.event_id)
     - If count >= settings.alert_confirmation_checks → alert, mark_alerted
     - Else → log debug "Awaiting confirmation: {event_id} ({count}/{threshold})"

3. At END of missing events loop, call:
   tracker.clear_not_missing(current_missing_event_ids)
   This ensures events that reappeared get their count reset

4. Update log message to show events pending confirmation count

Note: Competition comparison should be case-insensitive (event.competition.lower() in [c.lower() for c in top_competitions]).
  </action>
  <verify>Run `python -m live_coverage_bot` briefly, observe logs show "Awaiting confirmation" for non-top competition events on first detection. Top competition events should alert immediately.</verify>
  <done>Non-top events require N consecutive missing checks before alerting. Top competitions bypass delay.</done>
</task>

</tasks>

<verification>
Before declaring plan complete:
- [ ] `python -c "from live_coverage_bot.config import Settings; ..."` shows new settings
- [ ] AlertTracker.record_missing() tracks counts correctly
- [ ] AlertTracker.clear_not_missing() resets counts for reappeared events
- [ ] MonitoringLoop uses confirmation threshold for non-top competitions
- [ ] Top competitions alert immediately (first detection)
- [ ] No TypeScript/Python errors
</verification>

<success_criteria>

- All tasks completed
- All verification checks pass
- No errors introduced
- False positive reduction working: events must be missing N times before alert
- Top competitions bypass: EPL, Champions League etc. alert immediately
</success_criteria>

<output>
After completion, create `.planning/phases/7-alert-delay-system/7-01-SUMMARY.md`:

# Phase 7 Plan 01: Alert Delay System Summary

**[Substantive one-liner about what was built]**

## Accomplishments
- [Key outcomes]

## Files Created/Modified
- `path/to/file.ts` - Description

## Decisions Made
[Key decisions and rationale]

## Issues Encountered
[Problems and resolutions, or "None"]

## Next Step
Phase 7 complete, ready for Phase 8 (Docker Deployment)
</output>
