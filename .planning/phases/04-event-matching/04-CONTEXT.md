# Phase 4: Event Matching - Context

**Gathered:** 2026-03-12
**Status:** Ready for planning

<vision>
## How This Should Work

Simple provider ID comparison. When SportyBet has a live event and BetPawa doesn't, flag it as missing. No fancy matching algorithms — just straightforward ID lookup.

When a missing event is detected, return it with full context: teams, score, current time, and league. This gives the alert system everything it needs to send meaningful notifications.

</vision>

<essential>
## What Must Be Nailed

- **Never miss a match** — if SportyBet has an event, we must detect when BetPawa is missing it
- **No false positives** — only flag truly missing events, don't spam with false alarms from ID mismatches
- **Fast comparison** — quick matching so alerts go out immediately when events are missing

</essential>

<boundaries>
## What's Out of Scope

- Fuzzy name matching — provider IDs only, no team name comparisons
- Historical tracking — just current live state, no match history or trends
- Odds comparison — only checking event presence, not markets or pricing

</boundaries>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches based on the existing provider ID extraction logic documented in the roadmap.

</specifics>

<notes>
## Additional Context

The matching logic foundation is already defined:
- Extract provider ID from SportyBet `sr:match:{id}` (strip `11111111` prefix for GeniusSports)
- Match against BetPawa `widgets[].id` for SPORTRADAR or GENIUSSPORTS types
- Cross-provider matching supported (SportyBet uses SR, BetPawa uses GS for same event)

</notes>

---

*Phase: 04-event-matching*
*Context gathered: 2026-03-12*
