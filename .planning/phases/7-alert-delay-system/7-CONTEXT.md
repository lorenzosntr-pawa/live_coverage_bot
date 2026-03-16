# Phase 7: Alert Delay System - Context

**Gathered:** 2026-03-16
**Status:** Ready for planning

<vision>
## How This Should Work

When an event is detected as missing from BetPawa, don't alert immediately. Instead, track it and wait to see if it's consistently missing across multiple consecutive checks. Only alert once the event has been confirmed missing for X checks in a row.

Top competitions (Premier League, Champions League, etc.) bypass this entirely - they alert on first detection because those are high-profile and can't wait.

Once an event triggers an alert, that's it - one notification, no repeats. The team has been notified; no need to spam them while the event remains missing.

</vision>

<essential>
## What Must Be Nailed

- **Reduce false positive noise** - The main goal is fewer spurious alerts. Only notify when an event is persistently missing, not on temporary sync glitches
- **Never miss top events** - Top competitions always alert immediately, no confirmation delay. EPL, Champions League matches can't wait

Both are equally non-negotiable.

</essential>

<boundaries>
## What's Out of Scope

- No UI or dashboard for managing delays or viewing pending alerts
- No per-competition or per-country configurable thresholds - global settings only
- No reminder/repeat alerts for events that stay missing

</boundaries>

<specifics>
## Specific Ideas

- Number of required consecutive missing checks should be configurable (e.g., `ALERT_CONFIRMATION_CHECKS=3` environment variable)
- Top competitions list from roadmap: EPL, LaLiga, Ligue 1, Bundesliga, Serie A, UCL, UEL, UECL

</specifics>

<notes>
## Additional Context

This is a noise reduction phase. The current system alerts on first detection, which can cause false positives when BetPawa is just slightly behind SportyBet in adding events. The confirmation approach ensures we only alert on genuinely missing coverage.

</notes>

---

*Phase: 7-alert-delay-system*
*Context gathered: 2026-03-16*
