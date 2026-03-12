# Issues

## Open Enhancements

_None._

---

## Closed Enhancements

### ISS-001: Add country name to tournament display in alerts
**Discovered:** Phase 7 planning - 2026-03-12
**Resolved:** 2026-03-12 - Inserted as Phase 6.1 (Country in Alerts)
**Type:** UX

### ISS-002: Filter alerts to only in-play matches
**Discovered:** Phase 7 planning - 2026-03-12
**Resolved:** 2026-03-12 - Inserted as Phase 6.3 (In-Play Filter)
**Type:** UX / False-positive reduction

### ISS-003: Only alert for matches BetPawa offered pre-match
**Discovered:** Post Phase 6.3 - 2026-03-12
**Resolved:** 2026-03-12 - Inserted as Phase 6.4 (Pre-match Cache)
**Type:** False-positive reduction
**Details:** Current logic alerts for any match on SportyBet but not BetPawa. This creates noise for matches BetPawa never intended to offer. Solution: cache BetPawa pre-match offerings, only alert when a cached match goes missing from live.
