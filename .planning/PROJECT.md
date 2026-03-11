# Live Coverage Bot

## What This Is

A live event coverage comparison tool that monitors SportyBet and BetPawa for live football matches, alerting via Slack when events are live on SportyBet but missing from BetPawa. Designed for near real-time detection (30-60s) with Docker deployment.

## Core Value

Reliable detection of missing live events in priority leagues — never miss an event that should be on BetPawa.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] SportyBet live event monitoring (30s polling, priority leagues filter)
- [ ] BetPawa live event monitoring (matching polling frequency)
- [ ] Event comparison engine to detect SportyBet events missing from BetPawa
- [ ] Slack webhook alerts with match info (teams, league, score, time) and SportyBet link
- [ ] Configurable priority leagues list (YAML/JSON config)
- [ ] Duplicate alert prevention (light tracking to avoid re-alerting same event)
- [ ] Docker container deployment

### Out of Scope

- Dynamic league flagging via Slack commands — deferred to future version
- Odds comparison — only checking event presence, not odds values
- Other sports — football only for v1
- Full Slack bot with slash commands — webhook only for v1
- Historical data persistence/analytics — only tracking needed for deduplication

## Context

**Reference code**: User provided SportyBet scraper code as reference architecture:
- Async Python with aiohttp for API calls
- SQLite for data storage
- Configurable tournament filtering via YAML
- Discovery loop (30s) + polling loop (12s) pattern
- Clean separation: api_client, scraper, processor, config modules

**BetPawa API**: User has knowledge of the API structure (endpoints, headers, response formats).

**Architecture approach**: Build on patterns from SportyBet scraper — async Python, similar polling architecture, extend to support dual-source monitoring and comparison.

## Constraints

- **Runtime**: Docker container deployment
- **Alert latency**: Near real-time detection (30-60 seconds from event going live)
- **Slack integration**: Incoming webhook (no bot token required for v1)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Fixed league list for v1 | Simplify scope, add dynamic flagging later | — Pending |
| Webhook over full Slack bot | Faster to ship, bot features can layer on | — Pending |
| Light tracking over full DB | Only need deduplication, not analytics | — Pending |
| Python + async | Match reference code patterns, proven for this use case | — Pending |

---
*Last updated: 2026-03-11 after initialization*
