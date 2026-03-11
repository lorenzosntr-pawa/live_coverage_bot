# Phase 3: BetPawa Client - Context

**Gathered:** 2026-03-11
**Status:** Ready for planning

<vision>
## How This Should Work

The BetPawa client fetches live football events and extracts exactly what the matching engine needs. Rather than mirroring SportyBet's structure or preserving BetPawa's internal format, it should be optimized for BetPawa's API — pulling out provider IDs (SPORTRADAR/GENIUSSPORTS), teams, scores, and match time.

The client serves the matching engine. Clean extraction, not format preservation.

</vision>

<essential>
## What Must Be Nailed

- **Reliable provider ID extraction** — SPORTRADAR and GENIUSSPORTS widget IDs are what make matching work
- **Consistent data model** — Output should work alongside SportyBet data in the matching engine
- **Solid error handling** — The monitoring loop depends on this client being reliable

All three are equally important — they work together to make a reliable client.

</essential>

<boundaries>
## What's Out of Scope

- Cross-provider normalization — that's Phase 4 (matching engine)
- Comparing to SportyBet data — client just extracts BetPawa data cleanly
- The matching logic itself — client's job ends at extraction

</boundaries>

<specifics>
## Specific Ideas

- Reference the BetPawa API documentation in the project — details are already documented there
- Use similar async patterns to SportyBet client (httpx, error handling) for consistency

</specifics>

<notes>
## Additional Context

This is the counterpart to the SportyBet client completed in Phase 2. Together they feed into Phase 4's matching engine. The API documentation in the project should guide the implementation approach.

</notes>

---

*Phase: 03-betpawa-client*
*Context gathered: 2026-03-11*
