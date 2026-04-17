# Live Coverage Bot — Channel Guide

This bot monitors BetPawa football events as they transition from prematch to live. It alerts this channel when something goes wrong — late events, missing markets, or unusual odds shifts.

---

## What the bot watches

The bot tracks every football event on BetPawa starting **3 hours before kickoff**. It checks every 30 seconds whether the event has gone live. It also takes snapshots of the available markets at different points in time to detect anomalies.

---

## Alert types

### 1. Late event

An event hasn't gone live within **5 minutes** of its scheduled kickoff.

```
🟡 LATE — Arsenal vs Chelsea
📋 England Premier League | England
⏰ Kickoff: 15:00 UTC | Now: 7min late
🔌 SPORTRADAR #12345
🆔 BetPawa ID: 34690200
```

**What to check:** The event should appear in the live feed shortly. If it doesn't resolve within ~90 minutes, it gets marked as NEVER LIVE.

**Thread updates:** The bot posts updates in the thread as the situation evolves:
- `15:05 — ⚠️ Not live yet — 5min past kickoff`
- `15:12 — ✅ Went live — delay: 12min`

When the event finally goes live, the parent message updates to:

```
🟢 WENT LIVE — Arsenal vs Chelsea
📋 England Premier League | England
⏰ Kickoff: 15:00 UTC | Live at: 15:12 UTC (+12min)
🔌 SPORTRADAR #12345
🆔 BetPawa ID: 34690200
```

---

### 2. Never live

An event never appeared in the live feed after **90 minutes** past kickoff.

```
🔴 NEVER LIVE — Arsenal vs Chelsea
📋 England Premier League | England
⏰ Kickoff: 15:00 UTC | Timed out after 90min
🔌 SPORTRADAR #12345
🆔 BetPawa ID: 34690200
```

**What to check:** This usually means the event was cancelled, postponed, or there's a provider issue. Check the provider dashboard.

---

### 3. Removed event

An event disappeared from the prematch feed **more than 30 minutes** before kickoff. This is unusual — events normally stay in prematch until they go live.

```
🗑️ REMOVED — Arsenal vs Chelsea
📋 England Premier League | England
⏰ Kickoff: 15:00 UTC | Removed 45min before kickoff
🔌 SPORTRADAR #12345
🆔 BetPawa ID: 34690200

Had 56 markets at time of removal
```

**What to check:** The bot keeps watching. If the event comes back (either in prematch or directly in live), it posts a thread update:

```
14:30 — ♻️ Reappeared in prematch after 15min
Markets: 56 before removal → 48 now
```

Or if it reappears directly as live:

```
15:01 — ♻️ Reappeared in live after 20min
Markets: 56 before removal → 42 now
⏱ 1' | First Half | 0-0
```

---

### 4. Market anomaly

When an event goes live, the bot compares its live markets against the prematch snapshot. An alert fires if:
- **Less than 50%** of prematch markets are still available live
- A **key market** is missing (1X2, Over/Under, BTTS, Double Chance, 1X2 1H)
- Any odds shifted by **more than 30%**

```
📊 MARKET ANOMALY — Arsenal vs Chelsea
📋 England Premier League | England
⏰ Kickoff: 15:00 UTC | Went live on time
🔌 SPORTRADAR #12345
🆔 BetPawa ID: 34690200

56 prematch markets → 22 live (39% retention)
❌ 34 markets dropped, 8 selections shifted > 5%
⚠️ Key market dropped: 1X2 - FT, Total Score Over/Under - FT
```

**Thread details:** The bot immediately posts a detailed breakdown in the thread:

**Missing markets** (key markets highlighted at the top, separated from the rest):
```
❌ *Missing markets (34 dropped):*
  ⚠️ 1X2 - FT  `KEY`
  ⚠️ Total Score Over/Under - FT  `KEY`
  ⚠️ Double Chance - FT  `KEY`

  • Correct Score - FT
  • Half Time/Full Time
  • Asian Handicap - FT
  • Draw No Bet - FT
  _... and 27 more_
```

**Odds shifts** (sorted by magnitude — biggest moves first, with handicap values on Over/Under lines):
```
📊 *Odds shifts (prematch → live):*

  *Win to Nil Away Team - FT:*
    Yes  `5.79` → `7.62`  *+31.6%*

  *Total Score Over/Under - FT:*
    Under (2.5)  `2.90` → `2.69`  *-7.2%*
    Over (2.5)  `2.19` → `2.05`  *-6.4%*

  *1X2 - FT:*
    1  `1.76` → `1.66`  *-5.7%*

  *Double Chance - FT:*
    X2  `1.94` → `2.06`  *+6.2%*
```

Only shifts above 5% are shown. Markets are sorted so the biggest shifts appear first.

---

### 5. Follow-up snapshots (+2min, +5min)

Markets can be slow to come online. The bot takes **three snapshots** after an event goes live: immediately, at +2 minutes, and at +5 minutes.

If an initial anomaly was detected, the follow-up snapshots post updates in the same thread:

```
⏱ 4' | First Half | 0-0
📊 Snapshot +2min: 22 → 31 markets (39% → 55% retention)
✅ Recovered: Double Chance - FT, Correct Score - FT
❌ Still missing: 1X2 - FT ⚠️ KEY, Half Time/Full Time
```

And at +5 minutes:

```
⏱ 7' | First Half | 0-0
📊 Snapshot +5min: 31 → 38 markets (55% → 68% retention)
✅ Recovered: Half Time/Full Time
❌ Still missing: 1X2 - FT ⚠️ KEY
```

**Match state context:** Every follow-up includes the current score and match minute. If a goal was scored in the first minutes, that explains why some markets may be temporarily suspended — this is normal and not a real issue.

---

## Understanding the match state line

```
⏱ 33' | First Half | 1-0
```

- **33'** — match minute
- **First Half** — current period
- **1-0** — current score (home - away)

This helps distinguish "markets missing because of a provider issue" from "markets temporarily suspended because something happened in the match" (e.g., a goal, red card, or VAR review).

---

## Weekly report

Every **Tuesday at 08:00 UTC**, the bot posts a summary of the previous week (Tue-Mon) to the summary channel:

- Total events tracked
- On-time vs late vs never-live breakdown
- Average and worst delays
- Per-provider stats (SportRadar vs GeniusSports)
- Top competitions with issues
- Market retention averages (using the 5-minute snapshot for accuracy)
- Key markets most frequently dropped

---

## Key things to know

- **Events within 30 minutes of kickoff** that disappear from prematch are NOT flagged as removed — this is normal BetPawa behavior during the transition window
- **The 5-minute snapshot** is the most reliable for market retention — the initial snapshot often shows fewer markets because they're still loading
- **Odds shifts during live play are normal** — if the match state shows a goal just happened, markets will temporarily suspend and odds will shift when they reopen
- **Provider IDs** (SPORTRADAR #xxx, GENIUSSPORTS #xxx) help cross-reference with the provider dashboards

---

## Questions?

If you see an alert and aren't sure what to do, check the thread for follow-up context. The bot provides all the detail you need to decide if this is a real issue or expected behavior.
