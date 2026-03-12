# Roadmap: Live Coverage Bot

## Overview

Build a live event coverage comparison tool that monitors SportyBet and BetPawa for live football matches, alerts via Slack when events are missing from BetPawa. Starting with foundation and API clients, progressing through matching engine and alerts, finishing with containerized deployment.

## Domain Expertise

None

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

- [x] **Phase 1: Foundation** - Project structure, dependencies, config schema
- [x] **Phase 2: SportyBet Client** - API client for live event monitoring
- [x] **Phase 3: BetPawa Client** - API client for live event monitoring
- [x] **Phase 4: Event Matching** - Comparison engine with provider ID matching
- [x] **Phase 5: Slack Alerts** - Webhook integration with formatted alerts
- [x] **Phase 6: Monitoring Loop** - Polling orchestration and deduplication
- [x] **Phase 6.1: Country in Alerts** - INSERTED - Add country name to tournament display
- [x] **Phase 6.2: SRL Filter & Provider Info** - INSERTED - Filter SRL matches, add provider info to alerts
- [x] **Phase 6.3: In-Play Filter** - INSERTED - Only alert on matches where minute is set
- [x] **Phase 6.4: Pre-match Cache** - INSERTED - Only alert for matches BetPawa had pre-match
- [x] **Phase 6.5: Human-Readable Logging** - INSERTED - Log tournaments and events in readable format

## Phase Details

### Phase 1: Foundation
**Goal**: Establish project structure, async framework, config schema for priority leagues
**Depends on**: Nothing (first phase)
**Research**: Unlikely (patterns from reference code)
**Plans**: 1

### Phase 2: SportyBet Client
**Goal**: API client to fetch live football events with tournament filtering
**Depends on**: Phase 1
**Research**: Unlikely (reference code provides implementation)
**Plans**: 1

### Phase 3: BetPawa Client
**Goal**: API client to fetch live football events from BetPawa
**Depends on**: Phase 1
**Research**: Unlikely (API documented in project)
**Plans**: 1

### Phase 4: Event Matching
**Goal**: Compare events via provider ID matching (SportRadar/GeniusSports IDs), detect missing events
**Depends on**: Phase 2, Phase 3
**Research**: Unlikely (ID extraction is straightforward)
**Plans**: TBD

**Matching logic**:
- Extract provider ID from SportyBet `sr:match:{id}` (strip `11111111` prefix for GeniusSports)
- Match against BetPawa `widgets[].id` for SPORTRADAR or GENIUSSPORTS types
- Cross-provider matching supported (SportyBet uses SR, BetPawa uses GS for same event)

### Phase 5: Slack Alerts
**Goal**: Webhook integration sending formatted match alerts with teams, league, score, time
**Depends on**: Phase 4
**Research**: Unlikely (Slack webhooks are straightforward)
**Plans**: TBD

### Phase 6: Monitoring Loop
**Goal**: Polling orchestration (30s interval), duplicate alert prevention, graceful error handling
**Depends on**: Phase 5
**Research**: Unlikely (async polling pattern from reference code)
**Plans**: TBD

### Phase 6.1: Country in Alerts (INSERTED)
**Goal**: Add country name to tournament display in Slack alerts for better context
**Depends on**: Phase 6
**Research**: Likely (need to verify API response fields for country data)
**Plans**: 1

**Implementation:**
- Add `country_name` field to `LiveEvent` model
- Extract country from SportyBet tournament object (check for category field)
- Extract country from BetPawa competition object
- Update Slack message format: "Country - Competition | Score: X-X"

### Phase 6.2: SRL Filter & Provider Info (INSERTED)
**Goal**: Filter out SRL test matches from alerts, add provider type and ID to alert messages
**Depends on**: Phase 6.1
**Research**: Unlikely (straightforward filtering and string formatting)
**Plans**: 1

**Implementation:**
- Filter out matches where both home AND away team names contain "SRL" (SportyBet test matches)
- Add provider type (SPORTRADAR/GENIUSSPORTS) to Slack alert message
- Include provider ID in alert for debugging/reference

### Phase 6.3: In-Play Filter (INSERTED)
**Goal**: Only send alerts for matches that have actually started (minute is set), reducing false positives from timing differences between platforms
**Depends on**: Phase 6.2
**Research**: Unlikely (simple condition check)
**Plans**: 1

**Implementation:**
- Add filter in monitoring loop: skip alerts where `event.minute` is None
- Matches showing as "live" but not yet started won't trigger alerts
- Prevents false positives when BetPawa switches to live later than SportyBet

### Phase 6.4: Pre-match Cache (INSERTED)
**Goal**: Only alert for matches that BetPawa offered during pre-match, reducing false positives for matches BetPawa never intended to cover
**Depends on**: Phase 6.3
**Research**: Likely (need to discover BetPawa pre-match API endpoint)
**Plans**: TBD

**Implementation:**
- Discover/implement BetPawa pre-match API endpoint
- Cache pre-match offerings with match IDs and kickoff times
- On live check: only alert if match was in pre-match cache
- Handle cache expiry (matches expire after kickoff + buffer)

### Phase 6.5: Human-Readable Logging (INSERTED)
**Goal**: Make poll cycle logs show human-readable tournament and event lists instead of raw HTTP details
**Depends on**: Phase 6.4
**Research**: Unlikely (straightforward logging improvements)
**Plans**: TBD

**Implementation:**
- Suppress verbose httpx request logging (set to WARNING or use custom filter)
- Log grouped summary by tournament: "England - Premier League: 3 events"
- Log individual events with teams, score, minute: "Man Utd vs Liverpool | 2-1 | 45'"
- Separate sections for SportyBet, BetPawa, and Missing events
- Keep summary line: "Poll: 11 SportyBet, 8 BetPawa, 3 missing, 0 alerts"
- Pre-match cache refresh: log readable event list with teams, tournament, kickoff time
  - Return full event data from get_upcoming_events() not just provider IDs
  - Example: "Cached: Arsenal vs Chelsea (England - Premier League) @ 15:00"
  - Group by tournament for readability
  - IMPORTANT: Preserve both SPORTRADAR and GENIUSSPORTS IDs per event (same match can have different provider types between SportyBet and BetPawa)

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 6.1 → 6.2 → 6.3 → 6.4 → 6.5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 1/1 | Complete | 2026-03-11 |
| 2. SportyBet Client | 1/1 | Complete | 2026-03-11 |
| 3. BetPawa Client | 1/1 | Complete | 2026-03-11 |
| 4. Event Matching | 1/1 | Complete | 2026-03-12 |
| 5. Slack Alerts | 1/1 | Complete | 2026-03-12 |
| 6. Monitoring Loop | 1/1 | Complete | 2026-03-12 |
| 6.1. Country in Alerts | 1/1 | Complete | 2026-03-12 |
| 6.2. SRL Filter & Provider Info | 1/1 | Complete | 2026-03-12 |
| 6.3. In-Play Filter | 1/1 | Complete | 2026-03-12 |
| 6.4. Pre-match Cache | 1/1 | Complete | 2026-03-12 |
| 6.5. Human-Readable Logging | 1/1 | Complete | 2026-03-12 |
