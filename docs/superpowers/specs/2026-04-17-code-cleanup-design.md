# Code Cleanup — Design Spec

**Date:** 2026-04-17
**Scope:** General code quality pass before adding new monitoring features

## Goal

Clean up the existing codebase so that the upcoming features (enhanced REMOVED tracking, detailed market anomaly threads, multi-snapshot live retention) build on solid, well-typed, well-structured code.

## High Priority

### 1. Fix Exception Handling

**Files:** `clients/betpawa.py`, `core/loop.py`, `core/market_snapshotter.py`, `__main__.py`

**Problem:** Broad `except Exception as e:` blocks mask bugs. Silent `except ValueError: pass` in `betpawa.py:356,412` swallows parse failures without logging.

**Fix:**
- Replace `except Exception` with specific catches: `httpx.HTTPStatusError`, `httpx.RequestError`, `json.JSONDecodeError`, `KeyError`, `ValueError` as appropriate.
- Replace bare `pass` in `betpawa.py:356` (datetime parse) and `betpawa.py:412` (score parse) with `logger.warning(...)` calls so failures are visible.
- In `loop.py`, narrow the catch-all in `_poll_cycle()` and `_handle_market_comparison()` to catch only expected failure modes.

### 2. Extract Shared Slack Formatting

**File:** `clients/slack.py`

**Problem:** `format_parent_message()` (lines 38-45) and `format_market_anomaly_parent()` (lines 208-212) duplicate provider string, competition line, and kickoff string formatting.

**Fix:**
- Extract a private method `_format_event_header(event: TrackedEvent) -> tuple[str, str, str]` returning `(provider_str, competition_line, kickoff_str)`.
- Both formatting methods call this helper instead of duplicating the logic.

### 3. Type the Tracker Return Values

**File:** `core/tracker.py`

**Problem:** `check_transitions()` returns `list[dict[str, Any]]`, `_evaluate_transition()` returns `dict[str, Any] | None`, `_detect_removed_events()` returns `list[dict[str, Any]]`. No compile-time guarantee on dict keys.

**Fix:**
- Create dataclasses in `models/events.py`:
  - `TransitionResult` — fields: `event: TrackedEvent`, `old_status: EventStatus`, `new_status: EventStatus`, `delay_sec: float | None`, `details: str`
  - `RemovedResult` — fields: `event: TrackedEvent`, `old_status: EventStatus`, `details: str`
- Update tracker methods to return these types.
- Update `loop.py` transition handlers to use typed attributes instead of dict key access.

### 4. Break Up BetPawaClient

**File:** `clients/betpawa.py` (577 lines)

**Problem:** Single class mixes HTTP client management, API pagination, JSON response parsing, provider ID extraction, and score extraction. New features will add match-state parsing, making this worse.

**Fix:**
- Extract parsing logic into a separate module `clients/parsers.py`:
  - `parse_upcoming_event(data: dict) -> UpcomingEvent` — from `_parse_single_event()`
  - `parse_live_event(data: dict) -> LiveEvent` — from live event parsing in `get_live_events()`
  - `parse_event_markets(data: dict) -> list[Market]` — from nested loop in `get_event_markets()`
  - `extract_provider_ids(data: dict) -> list[ProviderID]` — from `_extract_provider_ids()`
  - `extract_scores(data: dict) -> dict` — from `_extract_scores()`
- `BetPawaClient` keeps HTTP/pagination concerns, calls parsers for data extraction.
- Each parser function is independently testable.

### 5. Type MarketComparison Details

**File:** `models/markets.py`

**Problem:** `MarketComparison.details` is typed as `dict` with no schema. Contains `dropped`, `added`, `odds_shifts` lists with specific structures.

**Fix:**
- Create typed structures:
  - `DroppedMarketDetail` — fields: `market_type_id: int`, `market_type_name: str`
  - `AddedMarketDetail` — fields: `market_type_id: int`, `market_type_name: str`
  - `OddsShiftDetail` — fields: `market_type_name: str`, `selection_name: str`, `selection_type_id: int`, `prematch_price: float`, `live_price: float`, `shift_pct: float`
  - `ComparisonDetails` — fields: `dropped: list[DroppedMarketDetail]`, `added: list[AddedMarketDetail]`, `odds_shifts: list[OddsShiftDetail]`
- Update `MarketComparison.details` type to `ComparisonDetails`.
- Update `market_comparator.py` to build typed objects instead of raw dicts.
- Update serialization in `market_repository.py` to handle the new types.

## Medium Priority

### 6. Extract Magic Numbers

**Files:** `core/reporter.py`, `clients/betpawa.py`

**Fix:**
- Add `on_time_threshold_seconds: int = 300` to `ThresholdConfig` in `config/models.py`. Replace hardcoded `300` in `reporter.py:63,64,174`.
- Extract API constants in `betpawa.py` to module-level: `DEFAULT_PAGE_SIZE = 100`, `FOOTBALL_CATEGORY_ID = "2"`, etc.

### 7. Standardize JSON Serialization

**Files:** `db/market_repository.py`, `models/markets.py`, `models/events.py`

**Fix:**
- Define a module-level constant `JSON_COMPACT = {"separators": (",", ":")}` in a shared location (e.g., `db/connection.py` or a small `utils.py`).
- Use it consistently in all `json.dumps()` calls.

### 8. Move Late Import to Module Level

**File:** `core/loop.py:285`

**Fix:** Move `from .market_reporter import MarketReporter` to the top of the file with other imports.

## Low Priority

### 9. Emoji Constants

**File:** `clients/slack.py`

**Fix:**
- Define constants at module level: `EMOJI_LATE = "\U0001f7e1"`, `EMOJI_LIVE = "\U0001f7e2"`, etc.
- Replace scattered emoji characters in format methods with constants.

### 10. Datetime Parse Helper

**Files:** `db/repository.py`, `db/market_repository.py`

**Fix:**
- Add a helper function `parse_dt(value: str | None) -> datetime | None` that wraps `datetime.fromisoformat()` with null handling.
- Replace repeated inline patterns.

## Out of Scope

- Adding new tests (will be done alongside new features)
- Docstring additions
- Changing assertion patterns (fine as developer guards)
- Refactoring MonitoringLoop further (will be addressed naturally when adding new features)
