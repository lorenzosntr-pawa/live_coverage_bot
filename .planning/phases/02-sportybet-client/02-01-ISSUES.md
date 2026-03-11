# UAT Issues: Phase 2 Plan 1

**Tested:** 2026-03-11
**Source:** .planning/phases/02-sportybet-client/02-01-SUMMARY.md
**Tester:** User via /gsd:verify-work

## Open Issues

[None]

## Resolved Issues

### UAT-002: Live matches show None for score and minute

**Discovered:** 2026-03-11
**Resolved:** 2026-03-11 - Fixed in 02-01-FIX2.md
**Commit:** d917d7b
**Phase/Plan:** 02-01
**Severity:** Major
**Feature:** Live event data parsing
**Root Cause:** Code looked for non-existent fields `gameInfo.liveScore` and `gameInfo.minute`. The actual API uses `setScore` and `playedSeconds` directly on the event object.
**Fix Applied:**
1. Changed score source from `gameInfo.liveScore` to `setScore`
2. Changed minute source from `gameInfo.minute` to `playedSeconds`
3. Parse MM:SS format from playedSeconds to extract minute value

### UAT-001: SportyBet API returns 404 Not Found

**Discovered:** 2026-03-11
**Resolved:** 2026-03-11 - Fixed in 02-01-FIX.md
**Commit:** 5d5f02b
**Phase/Plan:** 02-01
**Severity:** Blocker
**Feature:** Live events fetch
**Root Cause:** Base URL was incorrect. Should be `/api/ng/factsCenter` not `/api/ng`. Also required specific headers (clientid, platform, operid) and timestamp parameter.
**Fix Applied:**
1. Updated base_url in SportyBetConfig to `https://www.sportybet.com/api/ng/factsCenter`
2. Added DEFAULT_HEADERS with required API headers
3. Added timestamp parameter `_t` to requests
4. Added bizCode validation for API responses

---

*Phase: 02-sportybet-client*
*Plan: 01*
*Tested: 2026-03-11*
