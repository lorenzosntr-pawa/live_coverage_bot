# Live Coverage Bot

## What This Is

A live event coverage comparison tool that monitors SportyBet and BetPawa for live football matches, alerting via Slack when events are live on SportyBet but missing from BetPawa. Features provider ID matching, pre-match cache filtering, and human-readable logging.

## Core Value

Reliable detection of missing live events in priority leagues — never miss an event that should be on BetPawa.

## Requirements

### Validated

- ✓ SportyBet live event monitoring (30s polling, priority leagues filter) — v1.0
- ✓ BetPawa live event monitoring (matching polling frequency) — v1.0
- ✓ Event comparison engine to detect SportyBet events missing from BetPawa — v1.0
- ✓ Slack webhook alerts with match info (teams, league, score, time, provider) — v1.0
- ✓ Configurable priority leagues list (YAML config) — v1.0
- ✓ Duplicate alert prevention (in-memory tracking) — v1.0
- ✓ Pre-match cache filtering (only alert for matches BetPawa offered pre-match) — v1.0
- ✓ Human-readable logging grouped by tournament — v1.0

### Active

- [ ] Docker container deployment

### Out of Scope

- Dynamic league flagging via Slack commands — deferred to future version
- Odds comparison — only checking event presence, not odds values
- Other sports — football only for v1
- Full Slack bot with slash commands — webhook only for v1
- Historical data persistence/analytics — only tracking needed for deduplication
- SportyBet link in alerts — URL pattern not yet established

## Context

Shipped v1.0 with 1,485 LOC Python (16 files).
Tech stack: httpx, Pydantic v2, pydantic-settings, PyYAML.
Run via: `python -m live_coverage_bot`

**API Integrations:**
- SportyBet: Live events via `/liveOrPrematchEvents`, provider ID in event_id
- BetPawa: Live events via `/api/sportsbook/v3/events/lists/by-queries`, provider ID in widgets[]
- Slack: Incoming webhook for formatted alerts

**Event Matching:** Cross-platform comparison via SPORTRADAR/GENIUSSPORTS provider IDs extracted from both platforms. Events matched by (type, id) tuple.

## Constraints

- **Runtime**: Docker container deployment (pending)
- **Alert latency**: Near real-time detection (30-60 seconds from event going live)
- **Slack integration**: Incoming webhook (no bot token required for v1)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Fixed league list for v1 | Simplify scope, add dynamic flagging later | ✓ Good |
| Webhook over full Slack bot | Faster to ship, bot features can layer on | ✓ Good |
| In-memory deduplication | Events expire naturally when no longer live | ✓ Good |
| Python + async (httpx) | Modern async HTTP with better typing than aiohttp | ✓ Good |
| Provider ID matching | Exact matching via SportRadar/GeniusSports IDs | ✓ Good |
| Pre-match cache | Eliminates false positives for unintended events | ✓ Good |
| Pydantic PrivateAttr | Enables provider ID override for BetPawa events | ✓ Good |

---
*Last updated: 2026-03-12 after v1.0 milestone*
