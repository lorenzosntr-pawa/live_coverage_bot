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
- [ ] **Phase 6: Monitoring Loop** - Polling orchestration and deduplication
- [ ] **Phase 7: Docker Deployment** - Containerization and deployment

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

### Phase 7: Docker Deployment
**Goal**: Dockerfile, docker-compose, environment config for containerized deployment
**Depends on**: Phase 6
**Research**: Unlikely (standard containerization)
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 1/1 | Complete | 2026-03-11 |
| 2. SportyBet Client | 1/1 | Complete | 2026-03-11 |
| 3. BetPawa Client | 1/1 | Complete | 2026-03-11 |
| 4. Event Matching | 1/1 | Complete | 2026-03-12 |
| 5. Slack Alerts | 1/1 | Complete | 2026-03-12 |
| 6. Monitoring Loop | 0/TBD | Not started | - |
| 7. Docker Deployment | 0/TBD | Not started | - |
