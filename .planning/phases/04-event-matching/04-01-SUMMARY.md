---
phase: 04-event-matching
plan: 01
subsystem: core
tags: [event-matching, provider-ids, comparison-engine]

# Dependency graph
requires:
  - phase: 02-sportybet-client
    provides: LiveEvent model, provider ID extraction
  - phase: 03-betpawa-client
    provides: LiveEvent model with provider_ids_override
provides:
  - EventMatcher class for cross-platform comparison
  - find_missing_events() method
affects: [05-notification, future-phases]

# Tech tracking
tech-stack:
  added: []
  patterns: [set-based-id-comparison]

key-files:
  created:
    - src/live_coverage_bot/core/matcher.py
  modified:
    - src/live_coverage_bot/core/__init__.py

key-decisions:
  - "Used set of (type, id) tuples for O(1) provider ID lookup"
  - "Any matching provider ID considers events equivalent (flexible cross-provider matching)"

patterns-established:
  - "Provider ID comparison: Build set from target platform, check source events for ANY match"

issues-created: []

# Metrics
duration: 3 min
completed: 2026-03-12
---

# Phase 04-01 Summary: Event Matcher Module

**Event matching engine comparing SportyBet and BetPawa events using provider IDs**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-12T10:05:00Z
- **Completed:** 2026-03-12T10:08:00Z
- **Tasks:** 2/2
- **Files modified:** 2

## Accomplishments

1. **EventMatcher Class Created**
   - `find_missing_events(sportybet_events, betpawa_events)` method
   - Returns list of SportyBet events missing from BetPawa
   - Uses provider ID matching for cross-platform comparison

2. **Provider ID Matching Algorithm**
   - Builds set of (type, id) tuples from BetPawa events
   - For each SportyBet event, checks if ANY provider ID exists in BetPawa set
   - Events with no matching IDs are returned as "missing"

3. **Core Package Exports**
   - EventMatcher exported from `live_coverage_bot.core`
   - Clean `__all__` definition for explicit exports

## Task Commits

| Task | Description | Commit Hash |
|------|-------------|-------------|
| 1 | Create event matcher module | `a3a0dcd` |
| 2 | Update core package exports | `2d12a9d` |
| Fix | Remove unused ProviderID import | `0505cd0` |

## Files Created

- `src/live_coverage_bot/core/matcher.py` - EventMatcher class with find_missing_events()

## Files Modified

- `src/live_coverage_bot/core/__init__.py` - Added EventMatcher export

## Decisions Made

None - plan executed as written.

## Deviations from Plan

1. **Lint fix required**: Initial implementation imported `ProviderID` but it was unused (we access `pid.type` and `pid.id` directly). Added fix commit to remove unused import.

## Issues Encountered

None.

## Verification Results

- [x] `python -m ruff check src/live_coverage_bot/core/` passes
- [x] `python -c "from live_coverage_bot.core import EventMatcher"` works
- [x] EventMatcher.find_missing_events() returns empty list when all events match
- [x] EventMatcher.find_missing_events() returns missing events when BetPawa is missing coverage

## Next Phase Readiness

EventMatcher is complete and ready for integration:
- Can compare events from both platforms
- Returns missing events for notification
- Ready for Phase 05 (Notification) or CLI integration

---
*Phase: 04-event-matching*
*Completed: 2026-03-12*
