# Market Alert League Filtering

## Problem

Market anomaly alerts fire for every monitored match across all leagues. This creates too much noise in Slack. The weekly report covering all leagues is valuable for analysis, but real-time Slack alerts should be limited to top-tier competitions where the team will actually act on them.

## Solution

Add a configurable competition ID whitelist that gates **only** market anomaly Slack alerts. All other behavior (event lifecycle alerts, data collection, comparisons, reporting) remains unchanged for all leagues.

## Scope

### What changes

- Market anomaly Slack alerts (dropped markets, odds shifts, retention warnings) are only posted for events whose `competition_id` is in the configured whitelist
- `competition_id` is threaded through the data pipeline and stored in the DB

### What does NOT change

- Event lifecycle alerts (LATE, LIVE, NEVER_LIVE, REMOVED) — all matches
- Market snapshot collection — all matches
- Market comparison computation and DB storage — all matches
- Weekly reports and CSV exports — all matches

## Configuration

Add `alert_competition_ids` to `MarketsConfig`. An empty list means alert for everything (backwards-compatible default).

```yaml
markets:
  alert_competition_ids:
    - "11965"   # Premier League (England)
    - "12145"   # FA Cup (England)
    - "12097"   # Serie A (Italy)
    - "12243"   # Coppa Italia (Italy)
    - "12468"   # Super Cup (Italy)
    - "11998"   # Coupe de France (France)
    - "12127"   # Ligue 1 (France)
    - "11979"   # Copa del Rey (Spain)
    - "12039"   # LaLiga (Spain)
    - "12223"   # Super Cup (Spain)
    - "12110"   # Bundesliga (Germany)
    - "12389"   # DFB Pokal (Germany)
    - "12541"   # UEFA Champions League (International)
    - "12546"   # UEFA Europa League (International)
    - "12545"   # UEFA Europa Conference League (International)
    - "258194"  # FIFA World Cup (International)
```

In `config/models.py`:

```python
class MarketsConfig(BaseModel):
    alert_competition_ids: list[str] = []
    # ... existing fields unchanged
```

## Data Pipeline: Threading `competition_id`

The BetPawa API returns `competition.id` in event responses. Currently this is parsed for `LiveEvent` but not for `UpcomingEvent`, and it is not stored in `TrackedEvent` or the database.

### Changes by layer

1. **`clients/models.py`** — Add `competition_id: str = ""` to `UpcomingEvent`
2. **`clients/parsers.py`** — Parse `competition.id` in `parse_upcoming_event()`
3. **`clients/betpawa.py`** — Pass `competition_id` in `upcoming_to_tracker_feed()`
4. **`models/events.py`** — Add `competition_id: str = ""` to `TrackedEvent`
5. **`core/tracker.py`** — Set `competition_id` in `register_prematch_events()`
6. **`db/connection.py`** — Migration: `ALTER TABLE events ADD COLUMN competition_id TEXT DEFAULT ''`
7. **`db/repository.py`** — Include `competition_id` in `insert_event()` and `_row_to_event()`

Default value of `""` for existing DB rows means pre-migration events won't match the whitelist — correct behavior since we only care about filtering going forward.

## Alert Gating

Single choke point in `_handle_market_comparison()` in `core/loop.py`.

The gate is placed **after** the comparison is computed and written to the DB, but **before** any Slack messages are sent:

```python
# ... comparison + DB insert (unchanged) ...

alert_ids = self._settings.markets.alert_competition_ids
if alert_ids and event.competition_id not in alert_ids:
    logger.debug(
        "Skipping market alert for %s (%s) — not in alert leagues",
        event.betpawa_event_id, event.competition,
    )
    return

# ... Slack routing (unchanged) ...
```

Behavior:
- Empty `alert_competition_ids` = alert everything (backwards-compatible)
- Non-empty list = only alert listed competitions
- The check converts the list to a set lookup at call time; with 16 entries this is negligible

## File Change Summary

| File | Change |
|------|--------|
| `config/models.py` | Add `alert_competition_ids: list[str]` to `MarketsConfig` |
| `clients/models.py` | Add `competition_id: str` to `UpcomingEvent` |
| `clients/parsers.py` | Parse `competition.id` in `parse_upcoming_event()` |
| `clients/betpawa.py` | Pass `competition_id` in `upcoming_to_tracker_feed()` |
| `models/events.py` | Add `competition_id: str = ""` to `TrackedEvent` |
| `core/tracker.py` | Set `competition_id` in `register_prematch_events()` |
| `db/connection.py` | Add migration for `competition_id` column |
| `db/repository.py` | Include `competition_id` in insert and read |
| `core/loop.py` | Add whitelist gate in `_handle_market_comparison()` |
| `config.yaml` | Add `alert_competition_ids` with 16 league IDs |

## Testing

- Existing tests pass without changes (new field defaults to `""`)
- New unit tests for:
  - `parse_upcoming_event()` extracts `competition_id`
  - `upcoming_to_tracker_feed()` includes `competition_id`
  - `_handle_market_comparison()` skips Slack when competition not in whitelist
  - `_handle_market_comparison()` posts Slack when competition is in whitelist
  - Empty whitelist alerts everything (backwards compatibility)
