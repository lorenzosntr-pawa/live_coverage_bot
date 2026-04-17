# Monitoring Improvements — Design Spec

**Date:** 2026-04-17
**Scope:** Three interrelated features to improve transition tracking, market anomaly detail, and live retention accuracy.
**Prerequisite:** Code cleanup spec (2026-04-17-code-cleanup-design.md) must be completed first.

---

## Feature 1: Enhanced REMOVED Event Tracking

### Problem

Events sometimes vanish from the prematch feed and get marked REMOVED, then reappear in prematch or go live. The bot currently has a recovery path (REMOVED -> LIVE) but doesn't track what happens in between — no visibility into how long the gap was, whether the event came back to prematch first, or how markets changed.

### Design

#### 1.1 Continue Watching REMOVED Events

REMOVED events stay in the active tracking set. Every poll cycle, check both feeds:
- Did it reappear in the prematch feed?
- Did it appear in the live feed?

Stop watching after 24 hours (existing behavior).

#### 1.2 Pre-Removal Market Snapshot

When an event is about to be marked REMOVED, take a market snapshot first (phase: `PRE_REMOVAL`) if no recent prematch snapshot exists. This gives a baseline for comparison when the event reappears.

Add `PRE_REMOVAL` to the `SnapshotPhase` enum.

#### 1.3 New State Transitions

Add `REMOVED -> PREMATCH` as a valid transition (currently only `REMOVED -> LIVE` exists).

State changes logged with enriched details:

| Transition | Details |
|---|---|
| `* -> REMOVED` | "Disappeared from prematch at HH:MM, had X markets, Y min before kickoff" |
| `REMOVED -> PREMATCH` | "Reappeared in prematch at HH:MM after X min gap, now has Y markets (was Z before removal)" |
| `REMOVED -> LIVE` | "Reappeared directly in live at HH:MM after X min gap, with Y markets" |

#### 1.4 Slack Notifications

**On removal:** Post alert (existing behavior) but include market count in the message:
```
🗑️ REMOVED — Team A vs Team B
📋 Competition | Country
⏰ Kickoff: HH:MM UTC | Removed X min before kickoff
🔌 SPORTRADAR #12345
🔤 BetPawa ID: 34690200

Had 56 markets at time of removal
```

**On reappearance:** Thread reply on the existing alert:
```
HH:MM — ♻️ Reappeared in [prematch/live] after X min
Markets: 56 before removal → 48 now
[If live: ⏱ 1' | First Half | 0-0]
```

#### 1.5 Data Model Changes

- `SnapshotPhase` enum: add `PRE_REMOVAL`
- `TrackedEvent`: add `removed_at: datetime | None` and `pre_removal_market_count: int | None` fields
- Events table: add `removed_at` and `pre_removal_market_count` columns

---

## Feature 2: Detailed Market Anomaly Threads

### Problem

The current market anomaly alert shows only "Biggest odds shift: +X%" which is not actionable. Users need to see which specific markets are missing and how odds shifted per selection to understand whether the anomaly is real or expected.

### Design

#### 2.1 Parent Message Update

Replace the "Biggest odds shift" line with a summary count:
```
56 prematch markets → 22 live (39% retention)
❌ 34 markets dropped, 8 selections shifted > 5%
⚠️ Key market dropped: 1X2 - FT, Total Score Over/Under - FT
```

#### 2.2 Thread Reply — Missing Markets

```
❌ Missing markets (34 dropped):
  • 1X2 - FT ⚠️ KEY
  • Total Score Over/Under - FT ⚠️ KEY
  • Double Chance - FT ⚠️ KEY
  • Correct Score - FT
  • Half Time/Full Time
  • Asian Handicap - FT
  • Draw No Bet - FT
  ... and 27 more
```

Display rules:
- Always show all key markets that are missing (flagged with ⚠️ KEY).
- Show up to 7 non-key markets by name.
- If more than 7 non-key markets dropped, show count of remaining.

#### 2.3 Thread Reply — Per-Selection Odds Shifts

```
📊 Odds shifts (prematch → live):
  1X2 - 1H:
    Home  1.50 → 1.85 (+23.3%)
    Draw  3.20 → 2.90 (-9.4%)
    Away  4.00 → 4.50 (+12.5%)
  Both Teams To Score - FT:
    Yes   1.75 → 1.60 (-8.6%)
    No    2.00 → 2.25 (+12.5%)
```

Display rules:
- Show all selections where `abs(shift_pct) >= odds_shift_display_threshold` (configurable, default 5%).
- Group by market name.
- Show prematch price → live price with signed percentage.
- If no selections exceed the threshold, show "No significant odds shifts."

#### 2.4 Match State Context

Every thread reply includes match state at the top:
```
⏱ 3' | First Half | 0-0
```

Extracted from the BetPawa live event response `results` object:
- `results.display.minute` — match minute
- `results.display.currentPeriod.name` — period name
- `results.participantPeriodResults` — scores per team for "Full Time (Excluding Overtime)" period

#### 2.5 Configuration

Add to `markets` config:
```yaml
markets:
  odds_shift_display_threshold: 5.0  # % — minimum shift to show in thread detail
```

#### 2.6 Data Model Changes

- `MarketSnapshot`: add `match_state: MatchState | None` field
- New dataclass `MatchState`: `minute: str | None`, `period: str | None`, `home_score: int | None`, `away_score: int | None`
- Market snapshots table: add `match_state_json` column
- `ComparisonDetails.odds_shifts` already has per-selection data (from cleanup spec typing); the Slack formatter now uses it fully instead of summarizing to max.

#### 2.7 BetPawa Client Changes

- Add `parse_match_state(data: dict) -> MatchState | None` to the parsers module (created in cleanup spec).
- Called during live event fetching and market snapshot taking.
- Extracts minute, period, and FT scores from the `results` object.

---

## Feature 3: Multi-Snapshot Live Retention

### Problem

Markets can be slow to come online after an event goes live. Taking a single snapshot at detection time may show artificially low retention, triggering false anomaly alerts. Need to track how markets populate over the first few minutes.

### Design

#### 3.1 Three Live Snapshot Phases

New `SnapshotPhase` values:
- `LIVE_0` — taken immediately on live detection (replaces current `LIVE` phase)
- `LIVE_2` — taken ~2 minutes after going live
- `LIVE_5` — taken ~5 minutes after going live

Each includes match state (from Feature 2).

#### 3.2 Scheduling Follow-Up Snapshots

When an event transitions to LIVE:
1. Take `LIVE_0` snapshot immediately (existing behavior, renamed).
2. Record `first_seen_live` timestamp on the event (existing).
3. On subsequent poll cycles, the snapshotter checks: does this LIVE event need a `LIVE_2` or `LIVE_5` snapshot?
   - `LIVE_2`: eligible when `now - first_seen_live >= 2 minutes` and no `LIVE_2` snapshot exists.
   - `LIVE_5`: eligible when `now - first_seen_live >= 5 minutes` and no `LIVE_5` snapshot exists.

No new scheduler or timer needed — the existing poll loop (every 30s) naturally picks these up within one cycle of eligibility.

#### 3.3 Comparison Per Snapshot

Each live snapshot is compared against the same prematch baseline (latest prematch snapshot before going live). Three separate `MarketComparison` records in the database.

#### 3.4 Alert Flow

**If `LIVE_0` triggers an alert:**
1. Post anomaly parent message + detailed thread reply (Feature 2 format).
2. On `LIVE_2`: post thread update showing delta:
   ```
   ⏱ 4' | First Half | 0-0
   📊 Snapshot +2min: 22 → 31 markets (39% → 55% retention)
   ✅ Recovered: Double Chance - FT, Correct Score - FT
   ❌ Still missing: 1X2 - FT ⚠️ KEY, Half Time/Full Time
   New shifts: Away Win 3.50 → 4.10 (+17.1%)
   ```
3. On `LIVE_5`: same format, final assessment.

**If `LIVE_0` does NOT trigger an alert:**
- `LIVE_2` and `LIVE_5` snapshots are taken and stored silently (for weekly report data).
- If retention *drops* below thresholds on a later snapshot (things got worse), fire a new anomaly alert at that point.

#### 3.5 Weekly Report Uses Final Snapshot

The `MarketReporter` uses `LIVE_5` retention for aggregate stats. Falls back to `LIVE_2`, then `LIVE_0` if later snapshots are missing (e.g., bot restart, event ended quickly).

#### 3.6 Configuration

Add to `markets` config:
```yaml
markets:
  live_snapshot_offsets_minutes: [0, 2, 5]  # minutes after going live
```

Configurable so the user can adjust timing without code changes.

#### 3.7 Data Model Changes

- `SnapshotPhase` enum: add `LIVE_0`, `LIVE_2`, `LIVE_5` (deprecate old `LIVE` value, migrate to `LIVE_0`)
- Market snapshots table: `UNIQUE(event_id, phase)` constraint already handles multiple phases per event.
- `MarketComparison`: add `snapshot_phase: SnapshotPhase` field to track which live snapshot the comparison is for.
- Market comparisons table: add `snapshot_phase` column.

---

## Cross-Cutting Concerns

### Database Migrations

Both specs add columns to existing tables. Approach:
- Add columns with `ALTER TABLE ... ADD COLUMN` in the connection manager's schema initialization.
- New columns are nullable so existing rows remain valid.
- New `SnapshotPhase` values work with the existing TEXT column (phases stored as strings).

### Slack Message Length

Detailed thread replies could get long with many dropped markets or odds shifts. Mitigations:
- Cap displayed non-key markets at 7 + count.
- Odds shifts filtered by threshold (default 5%).
- If a single message exceeds Slack's 3000-char block limit, split into multiple thread replies.

### Performance

- Multi-snapshot adds 2 extra API calls per live event (at +2min and +5min). With typical event volumes this is negligible.
- REMOVED event watching adds no extra API calls — just checking existing feed results.
- Match state parsing is lightweight (already in the response payload).

### Backward Compatibility

- Existing `LIVE` phase snapshots in the database are left as-is. New snapshots use `LIVE_0`/`LIVE_2`/`LIVE_5`.
- Weekly report logic handles both old `LIVE` and new `LIVE_0` phases gracefully.
