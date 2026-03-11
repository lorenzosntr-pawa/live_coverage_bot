---
phase: 03-betpawa-client
plan: 01
subsystem: clients
tags: [api-client, betpawa, async, httpx, provider-ids]

# Dependency graph
requires:
  - phase: 01-foundation
    provides: config schema, project structure
  - phase: 02-sportybet-client
    provides: async client pattern, LiveEvent model
provides:
  - BetPawaClient async API client
  - Provider ID extraction from widgets
  - LiveEvent model with provider_ids_override support
affects: [04-event-matching]

# Tech tracking
tech-stack:
  added: []
  patterns: [provider-id-override-pattern]

key-files:
  created:
    - src/live_coverage_bot/clients/betpawa.py
  modified:
    - src/live_coverage_bot/clients/models.py
    - src/live_coverage_bot/clients/__init__.py

key-decisions:
  - "Used PrivateAttr for _provider_ids_override to allow BetPawa-style injection"
  - "Prefixed BetPawa event IDs with 'bp:' to distinguish from SportyBet IDs"
  - "Deduplicated provider IDs by (type, id) pair since BetPawa has duplicates"

patterns-established:
  - "Provider ID override: Set _provider_ids_override after LiveEvent instantiation for non-SportyBet sources"

issues-created: []

# Metrics
duration: 6 min
completed: 2026-03-11
---

# Phase 03-01 Summary: BetPawa Client Implementation

**Async BetPawa API client with SPORTRADAR/GENIUSSPORTS provider ID extraction from widgets**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-11T16:40:00Z
- **Completed:** 2026-03-11T16:46:19Z
- **Tasks:** 2/2
- **Files modified:** 3

## Accomplishments

1. **BetPawaClient Created**
   - Async client using httpx with 10s timeout
   - `get_live_events()` method fetches live football events
   - Context manager support (`async with` compatible)
   - `BetPawaError` custom exception

2. **Provider ID Extraction**
   - Parses widgets[] array for SPORTRADAR and GENIUSSPORTS IDs
   - Deduplicates by (type, id) pair
   - Sets `_provider_ids_override` on LiveEvent after instantiation

3. **Score and Minute Parsing**
   - Extracts home/away scores from `participantPeriodResults`
   - Uses `FULL_TIME_EXCLUDING_OVERTIME` period slug
   - Minute from `results.display.minute`

4. **LiveEvent Model Extended**
   - Added `_provider_ids_override` as Pydantic PrivateAttr
   - `provider_ids` computed field checks override first
   - Maintains backward compatibility with SportyBet event_id extraction

## Task Commits

| Task | Description | Commit Hash |
|------|-------------|-------------|
| 1+2 | Create BetPawa client and extend LiveEvent model | `c3d9c99` |

## Files Created

- `src/live_coverage_bot/clients/betpawa.py` - Async BetPawa API client

## Files Modified

- `src/live_coverage_bot/clients/models.py` - Added `_provider_ids_override` PrivateAttr to LiveEvent
- `src/live_coverage_bot/clients/__init__.py` - Exported BetPawaClient and BetPawaError

## Decisions Made

1. **Pydantic PrivateAttr over Field**: Used `PrivateAttr` instead of `Field` for `_provider_ids_override` since Pydantic v2 disallows leading underscores in field names
2. **Post-init attribute assignment**: Set `_provider_ids_override` after LiveEvent instantiation instead of passing as constructor argument
3. **Event ID prefix**: Used `bp:` prefix for BetPawa event IDs to distinguish from SportyBet's `sr:match:` format

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

1. **Pydantic underscore restriction**: Initial attempt to use `Field(default=None, exclude=True)` for `_provider_ids_override` failed - Pydantic v2 raises `NameError` for field names with leading underscores. Switched to `PrivateAttr` which is designed for this purpose.

## Verification Results

- [x] `python -m ruff check src/live_coverage_bot/clients/` passes
- [x] `python -c "from live_coverage_bot.clients import BetPawaClient, SportyBetClient, LiveEvent"` works
- [x] BetPawaClient can be instantiated with BetPawaConfig
- [x] LiveEvent._provider_ids_override works for injecting BetPawa provider IDs

## Next Phase Readiness

BetPawa client is complete and ready for Phase 04 (Event Matching):
- Both SportyBet and BetPawa clients return LiveEvent models
- Provider IDs extracted from both platforms
- Ready for cross-platform event comparison

---
*Phase: 03-betpawa-client*
*Completed: 2026-03-11*
