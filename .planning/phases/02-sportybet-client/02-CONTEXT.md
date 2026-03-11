# Phase 2: SportyBet Client - Context

**Gathered:** 2026-03-11
**Status:** Ready for planning

<vision>
## How This Should Work

A simplified, fetch-only client that retrieves live football events from SportyBet. Minimal design — just fetch events and return them cleanly. No extras, no complexity beyond what's needed.

The client fetches ALL live football events (not filtered by league). League filtering happens later in the matching phase, keeping this client simple and focused on reliable data retrieval.

</vision>

<essential>
## What Must Be Nailed

All three are equally critical:

- **Reliable event fetching** — Never miss live events. Handle retries, timeouts, and errors gracefully.
- **Fast response times** — Get events quickly so alerts happen promptly.
- **Clean event data** — Return well-structured data that's easy to match against BetPawa.

</essential>

<boundaries>
## What's Out of Scope

Standard exclusions apply:

- No caching or persistence — fetch fresh each time
- No historical data — only live events matter
- No league filtering — that's the matching phase's job
- No complex rate limiting logic — assume reasonable polling intervals

</boundaries>

<specifics>
## Specific Ideas

- Fetch all live football events, not filtered by priority leagues
- Keep the implementation minimal — mirror reference code patterns where useful
- Focus on the async structure established in Phase 1

</specifics>

<notes>
## Additional Context

This is the first of two API clients (SportyBet and BetPawa). The matching phase depends on both clients returning comparable, well-structured event data with provider IDs that can be cross-referenced.

</notes>

---

*Phase: 02-sportybet-client*
*Context gathered: 2026-03-11*
