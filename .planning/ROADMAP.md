# Roadmap: Live Coverage Bot

## Overview

Build a live event coverage comparison tool that monitors SportyBet and BetPawa for live football matches, alerts via Slack when events are missing from BetPawa. Starting with foundation and API clients, progressing through matching engine and alerts, finishing with containerized deployment.

## Milestones

- ✅ **v1.0 MVP** — Phases 1-6.5 (shipped 2026-03-12) — [Archive](milestones/v1.0-ROADMAP.md)
- 🚧 **v1.1 Alert Improvements** — Phase 7 (in progress)
- 📋 **v1.2 Deployment** — Phase 8+ (planned)

## Completed Milestones

<details>
<summary>✅ v1.0 MVP (Phases 1-6.5) — SHIPPED 2026-03-12</summary>

- [x] Phase 1: Foundation (1/1 plan) — completed 2026-03-11
- [x] Phase 2: SportyBet Client (1/1 plan + 2 fix) — completed 2026-03-11
- [x] Phase 3: BetPawa Client (1/1 plan + 1 fix) — completed 2026-03-11
- [x] Phase 4: Event Matching (1/1 plan) — completed 2026-03-12
- [x] Phase 5: Slack Alerts (1/1 plan) — completed 2026-03-12
- [x] Phase 6: Monitoring Loop (1/1 plan) — completed 2026-03-12
- [x] Phase 6.1: Country in Alerts (1/1 plan) — completed 2026-03-12
- [x] Phase 6.2: SRL Filter & Provider Info (1/1 plan) — completed 2026-03-12
- [x] Phase 6.3: In-Play Filter (1/1 plan) — completed 2026-03-12
- [x] Phase 6.4: Pre-match Cache (1/1 plan) — completed 2026-03-12
- [x] Phase 6.5: Human-Readable Logging (1/1 plan) — completed 2026-03-12

**See:** [v1.0-ROADMAP.md](milestones/v1.0-ROADMAP.md) for full details

</details>

## Current Milestone

### 🚧 v1.1 Alert Improvements (In Progress)

**Milestone Goal:** Reduce false positive alerts by adding configurable delay with top competition bypass

#### Phase 7: Alert Delay System

**Goal**: Add configurable alert delay with top competition exclusions
**Depends on**: v1.0 complete
**Research**: Unlikely (internal patterns, extending existing config/logic)
**Plans**: 1/1

Plans:
- [x] 7-01: Alert delay with top competition bypass — completed 2026-03-16

**Scope:**
- `alert_delay_minutes` config option (default: 5 minutes)
- `top_competitions` config list for competitions that bypass delay
- Track first-seen timestamp for each missing event
- Only alert after delay elapsed (immediate for top competitions)

**Top Competitions (bypass delay):**
- England Premier League
- Spain LaLiga
- France Ligue 1
- Germany Bundesliga
- Italy Serie A
- UEFA Champions League
- UEFA Europa League
- UEFA Conference League

## Future Phases

### 📋 v1.2 Deployment (Planned)

- [ ] Phase 8: Docker Deployment — Containerization with environment config

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 6.1 → 6.2 → 6.3 → 6.4 → 6.5 → 7 → 8

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation | v1.0 | 1/1 | Complete | 2026-03-11 |
| 2. SportyBet Client | v1.0 | 1/1 | Complete | 2026-03-11 |
| 3. BetPawa Client | v1.0 | 1/1 | Complete | 2026-03-11 |
| 4. Event Matching | v1.0 | 1/1 | Complete | 2026-03-12 |
| 5. Slack Alerts | v1.0 | 1/1 | Complete | 2026-03-12 |
| 6. Monitoring Loop | v1.0 | 1/1 | Complete | 2026-03-12 |
| 6.1. Country in Alerts | v1.0 | 1/1 | Complete | 2026-03-12 |
| 6.2. SRL Filter & Provider Info | v1.0 | 1/1 | Complete | 2026-03-12 |
| 6.3. In-Play Filter | v1.0 | 1/1 | Complete | 2026-03-12 |
| 6.4. Pre-match Cache | v1.0 | 1/1 | Complete | 2026-03-12 |
| 6.5. Human-Readable Logging | v1.0 | 1/1 | Complete | 2026-03-12 |
| 7. Alert Delay System | v1.1 | 1/1 | Complete | 2026-03-16 |
| 8. Docker Deployment | v1.2 | 0/? | Not started | - |
