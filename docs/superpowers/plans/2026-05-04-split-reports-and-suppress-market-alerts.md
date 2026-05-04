# Split Reports & Suppress Market Alerts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Suppress real-time market anomaly Slack alerts (keep data collection), split the weekly report into two independent messages (coverage + market retention), and upload CSVs as Slack file attachments threaded to each report message.

**Architecture:** Add `alerts_enabled` config flag to gate Slack posting in the market comparison handler. Modify `post_summary()` to return message `ts`. Add `upload_file()` to SlackClient using Slack's v2 file upload API. Split `_check_weekly_report()` into two flows. Add `generate_minimal_summary()` to MarketReporter. Remove retention % from top leagues block.

**Tech Stack:** Python 3.11+, pytest + pytest-asyncio, httpx, pydantic, Slack Web API (`files.getUploadURLExternal` + `files.completeUploadExternal`)

---

### Task 1: Add `alerts_enabled` config field

**Files:**
- Modify: `src/live_coverage_bot/config/models.py:73-88`
- Modify: `config.yaml:34-65`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_config.py`, add a test that `MarketsConfig` has an `alerts_enabled` field that defaults to `True`:

```python
class TestMarketsConfig:
    def test_alerts_enabled_defaults_true(self):
        config = MarketsConfig()
        assert config.alerts_enabled is True

    def test_alerts_enabled_can_be_disabled(self):
        config = MarketsConfig(alerts_enabled=False)
        assert config.alerts_enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::TestMarketsConfig -v`
Expected: FAIL — `MarketsConfig` has no `alerts_enabled` field.

- [ ] **Step 3: Add `alerts_enabled` to `MarketsConfig`**

In `src/live_coverage_bot/config/models.py`, add to the `MarketsConfig` class (after `enabled`):

```python
class MarketsConfig(BaseModel):
    """Market comparison feature configuration."""

    enabled: bool = True
    alerts_enabled: bool = True
    alert_competition_ids: list[str] = []
    # ... rest unchanged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py::TestMarketsConfig -v`
Expected: PASS

- [ ] **Step 5: Update `config.yaml`**

Add `alerts_enabled: false` under the `markets:` section, right after `enabled: true`:

```yaml
markets:
  enabled: true
  alerts_enabled: false
  alert_competition_ids:
```

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/config/models.py config.yaml tests/test_config.py
git commit -m "feat: add alerts_enabled config flag to MarketsConfig"
```

---

### Task 2: Gate real-time market alerts with `alerts_enabled`

**Files:**
- Modify: `src/live_coverage_bot/core/loop.py:408-457`
- Test: `tests/test_market_alert_filter.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_market_alert_filter.py`, add a test to the existing `TestMarketAlertLeagueFilter` class. Use the existing `_make_settings` helper which already accepts `**markets_overrides`:

```python
async def test_skips_slack_when_alerts_disabled(self, db):
    """Comparison saved to DB but Slack alert suppressed when alerts_enabled=False."""
    settings = _make_settings(
        alerts_enabled=False,
        alert_competition_ids=["11965"],
    )
    repo = EventRepository(db)
    market_repo = MarketRepository(db)

    loop = MonitoringLoop(settings)
    loop._db = db
    loop._repo = repo
    loop._market_repo = market_repo

    event = await _insert_test_event(repo, competition_id="11965")

    mock_slack = AsyncMock()
    now = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)

    with patch.object(loop, "_handle_initial_comparison", new_callable=AsyncMock) as mock_initial, \
         patch.object(loop, "_handle_followup_comparison", new_callable=AsyncMock) as mock_followup, \
         patch("live_coverage_bot.core.loop.compare_snapshots") as mock_compare, \
         patch.object(market_repo, "get_latest_prematch_snapshot", new_callable=AsyncMock) as mock_pre, \
         patch.object(market_repo, "get_snapshots_for_event", new_callable=AsyncMock) as mock_snaps, \
         patch.object(market_repo, "insert_comparison", new_callable=AsyncMock):

        mock_pre_snap = AsyncMock()
        mock_pre_snap.total_market_count = 100
        mock_pre.return_value = mock_pre_snap

        mock_live_snap = AsyncMock()
        mock_live_snap.phase = SnapshotPhase.LIVE_0
        mock_snaps.return_value = [mock_live_snap]

        mock_cmp = AsyncMock()
        mock_cmp.snapshot_phase = None
        mock_compare.return_value = mock_cmp

        await loop._handle_market_comparison(mock_slack, event.id, SnapshotPhase.LIVE_0, now)

        # Comparison was saved to DB
        market_repo.insert_comparison.assert_called_once()
        # But Slack handlers were NOT called
        mock_initial.assert_not_called()
        mock_followup.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_market_alert_filter.py::TestMarketAlertLeagueFilter::test_skips_slack_when_alerts_disabled -v`
Expected: FAIL — `_handle_initial_comparison` is still called because there's no `alerts_enabled` gate yet.

- [ ] **Step 3: Add the `alerts_enabled` gate**

In `src/live_coverage_bot/core/loop.py`, in `_handle_market_comparison()`, add the gate right after the DB insert succeeds (after line 435) and before the event lookup on line 437:

```python
        try:
            await self._market_repo.insert_comparison(cmp)
        except Exception as e:
            logger.warning("Market comparison insert failed for event %d: %s", event_id, e)
            return

        # Gate: skip Slack alerts if market alerts are disabled
        if not self._settings.markets.alerts_enabled:
            return

        event = await self._repo.get_by_id(event_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_market_alert_filter.py -v`
Expected: ALL tests PASS (new test + existing 3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/live_coverage_bot/core/loop.py tests/test_market_alert_filter.py
git commit -m "feat: gate real-time market alerts with alerts_enabled config flag"
```

---

### Task 3: Make `post_summary()` return `ts` and add `upload_file()` to SlackClient

**Files:**
- Modify: `src/live_coverage_bot/clients/slack.py:243-254`
- Test: `tests/test_slack.py`

- [ ] **Step 1: Write the failing test for `post_summary` returning `ts`**

In `tests/test_slack.py`, add to `TestSlackApiCalls`:

```python
async def test_post_summary_returns_ts(self, slack_config):
    client = SlackClient(slack_config)
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True, "ts": "1111.2222"}

    with patch.object(client._client, "post", return_value=mock_response):
        ts = await client.post_summary("weekly report text")
        assert ts == "1111.2222"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_slack.py::TestSlackApiCalls::test_post_summary_returns_ts -v`
Expected: FAIL — `post_summary` returns `None`, `assert None == "1111.2222"` fails.

- [ ] **Step 3: Modify `post_summary` to return `ts`**

In `src/live_coverage_bot/clients/slack.py`, change `post_summary`:

```python
async def post_summary(self, text: str) -> str:
    """Post a weekly summary message to the summary channel. Returns ts."""
    channel = self._config.summary_channel_id or self._config.channel_id
    response = await self._client.post(
        "/chat.postMessage",
        json={"channel": channel, "text": text},
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise SlackError(f"Slack API error: {data.get('error', 'unknown')}")
    return data["ts"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_slack.py::TestSlackApiCalls::test_post_summary_returns_ts -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for `upload_file`**

In `tests/test_slack.py`, add a new class:

```python
class TestSlackFileUpload:
    async def test_upload_file_calls_v2_api(self, slack_config):
        client = SlackClient(slack_config)

        # Step 1: getUploadURLExternal
        url_response = MagicMock()
        url_response.json.return_value = {
            "ok": True,
            "upload_url": "https://files.slack.com/upload/v1/abc123",
            "file_id": "F123ABC",
        }
        # Step 2: PUT to upload URL
        upload_response = MagicMock()
        upload_response.status_code = 200
        # Step 3: completeUploadExternal
        complete_response = MagicMock()
        complete_response.json.return_value = {"ok": True}

        with patch.object(
            client._client, "post",
            side_effect=[url_response, complete_response],
        ), patch.object(
            client._client, "put",
            return_value=upload_response,
        ):
            await client.upload_file(
                content="col1,col2\na,b\n",
                filename="test.csv",
                channel="C12345",
                thread_ts="1111.2222",
            )

        # Verify getUploadURLExternal was called
        first_call = client._client.post.call_args_list[0]
        assert "/files.getUploadURLExternal" in first_call[0][0]

        # Verify completeUploadExternal was called with file_id and thread
        second_call = client._client.post.call_args_list[1]
        assert "/files.completeUploadExternal" in second_call[0][0]
        body = second_call[1]["json"]
        assert body["files"] == [{"id": "F123ABC", "title": "test.csv"}]
        assert body["thread_ts"] == "1111.2222"

    async def test_upload_file_raises_on_url_error(self, slack_config):
        client = SlackClient(slack_config)
        url_response = MagicMock()
        url_response.json.return_value = {"ok": False, "error": "not_authed"}

        with patch.object(client._client, "post", return_value=url_response):
            with pytest.raises(SlackError, match="not_authed"):
                await client.upload_file(
                    content="data",
                    filename="f.csv",
                    channel="C12345",
                )
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_slack.py::TestSlackFileUpload -v`
Expected: FAIL — `SlackClient` has no `upload_file` method.

- [ ] **Step 7: Implement `upload_file`**

In `src/live_coverage_bot/clients/slack.py`, add the method to `SlackClient` (after `post_summary`):

```python
async def upload_file(
    self,
    content: str,
    filename: str,
    channel: str,
    thread_ts: str | None = None,
) -> None:
    """Upload a file to Slack using the v2 upload API."""
    content_bytes = content.encode("utf-8")

    # Step 1: Get upload URL
    response = await self._client.post(
        "/files.getUploadURLExternal",
        json={"filename": filename, "length": len(content_bytes)},
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise SlackError(f"Slack upload URL error: {data.get('error', 'unknown')}")

    upload_url = data["upload_url"]
    file_id = data["file_id"]

    # Step 2: Upload file content
    await self._client.put(
        upload_url,
        content=content_bytes,
        headers={"Content-Type": "text/csv"},
    )

    # Step 3: Complete upload
    complete_body: dict = {
        "files": [{"id": file_id, "title": filename}],
        "channel_id": channel,
    }
    if thread_ts:
        complete_body["thread_ts"] = thread_ts

    response = await self._client.post(
        "/files.completeUploadExternal",
        json=complete_body,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise SlackError(f"Slack upload complete error: {data.get('error', 'unknown')}")
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_slack.py::TestSlackFileUpload -v`
Expected: PASS

- [ ] **Step 9: Run all slack tests**

Run: `pytest tests/test_slack.py -v`
Expected: ALL PASS

- [ ] **Step 10: Commit**

```bash
git add src/live_coverage_bot/clients/slack.py tests/test_slack.py
git commit -m "feat: make post_summary return ts, add upload_file via Slack v2 API"
```

---

### Task 4: Add `generate_minimal_summary()` to MarketReporter

**Files:**
- Modify: `src/live_coverage_bot/core/market_reporter.py`
- Test: `tests/test_market_reporter.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_market_reporter.py`, add a new class:

```python
class TestMinimalSummary:
    async def test_generates_minimal_summary(self, reporter, event_repo, market_repo):
        await _seed(event_repo, market_repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        text = await reporter.generate_minimal_summary(start, end, retention_threshold=60.0)
        assert "Market Retention" in text
        assert "Events with market snapshots: 1" in text
        assert "Avg market retention:" in text

    async def test_minimal_summary_counts_significant_drops(self, reporter, event_repo, market_repo):
        """Events below retention threshold are counted as significant drops."""
        base = datetime(2026, 4, 14, 15, 0, tzinfo=UTC)
        event = TrackedEvent(
            betpawa_event_id="99002",
            home_team="Team C", away_team="Team D",
            competition="LaLiga", country="Spain",
            scheduled_kickoff=base, status=EventStatus.LIVE,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="67890")],
            first_seen_prematch=base, first_seen_live=base,
            transition_delay_sec=60,
        )
        eid = await event_repo.insert_event(event)
        await market_repo.insert_comparison(MarketComparison(
            event_id=eid,
            compared_at=base,
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0, markets_dropped=8, markets_kept=2,
            retention_pct=20.0, dropped_key_markets=["1X2 - FT"],
            max_odds_shift_pct=25.0, triggered_alert=True,
            details={"dropped": [], "added": [], "odds_shifts": []},
        ))

        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        text = await reporter.generate_minimal_summary(start, end, retention_threshold=60.0)
        assert "significant drops" in text.lower() or "Significant drops" in text
        assert "1" in text  # one event below threshold

    async def test_minimal_summary_empty_period(self, reporter, event_repo, market_repo):
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        text = await reporter.generate_minimal_summary(start, end, retention_threshold=60.0)
        assert "No market comparisons" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_market_reporter.py::TestMinimalSummary -v`
Expected: FAIL — `MarketReporter` has no `generate_minimal_summary` method.

- [ ] **Step 3: Implement `generate_minimal_summary`**

In `src/live_coverage_bot/core/market_reporter.py`, add the method to `MarketReporter` (after `generate_aggregate_csv`):

```python
async def generate_minimal_summary(
    self, start: datetime, end: datetime, retention_threshold: float
) -> str:
    """Return a minimal market retention summary for the weekly Slack message."""
    all_comparisons = await self._markets.get_comparisons_in_date_range(start, end)

    # Deduplicate: one comparison per event, prefer LIVE_5 > LIVE_2 > LIVE_0
    seen_events: set[int] = set()
    comparisons: list[MarketComparison] = []
    for cmp in all_comparisons:
        if cmp.event_id in seen_events:
            continue
        best = await self._markets.get_best_comparison_for_event(cmp.event_id)
        if best:
            comparisons.append(best)
            seen_events.add(cmp.event_id)

    if not comparisons:
        return "\U0001f4ca Market Retention Report\n\nNo market comparisons in this period."

    avg_retention = sum(c.retention_pct for c in comparisons) / len(comparisons)
    significant_drops = sum(
        1 for c in comparisons if c.retention_pct < retention_threshold
    )

    return "\n".join([
        "\U0001f4ca Market Retention Report",
        "",
        f"Events with market snapshots: {len(comparisons)}",
        f"Avg market retention: {avg_retention:.0f}%",
        f"Significant drops (retention <{retention_threshold:.0f}%): {significant_drops}",
    ])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_market_reporter.py::TestMinimalSummary -v`
Expected: PASS

- [ ] **Step 5: Run all market reporter tests**

Run: `pytest tests/test_market_reporter.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/core/market_reporter.py tests/test_market_reporter.py
git commit -m "feat: add generate_minimal_summary to MarketReporter"
```

---

### Task 5: Remove retention % from top leagues block

**Files:**
- Modify: `src/live_coverage_bot/core/market_reporter.py:153-231`
- Test: `tests/test_market_reporter.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_market_reporter.py`, add a new class:

```python
class TestTopLeaguesNoRetention:
    async def test_top_leagues_excludes_retention(self, reporter, event_repo, market_repo):
        """Top leagues block should NOT include market retention %."""
        base = datetime(2026, 4, 14, 15, 0, tzinfo=UTC)
        event = TrackedEvent(
            betpawa_event_id="99010",
            home_team="Team X", away_team="Team Y",
            competition="EPL", competition_id="11965",
            country="England",
            scheduled_kickoff=base, status=EventStatus.LIVE,
            provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="111")],
            first_seen_prematch=base, first_seen_live=base,
            transition_delay_sec=60,
        )
        eid = await event_repo.insert_event(event)

        # Insert a comparison so retention data exists
        await market_repo.insert_comparison(MarketComparison(
            event_id=eid,
            compared_at=base,
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0, markets_dropped=2, markets_kept=8,
            retention_pct=80.0, dropped_key_markets=[],
            max_odds_shift_pct=0.0, triggered_alert=False,
            details={"dropped": [], "added": [], "odds_shifts": []},
        ))

        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 16, tzinfo=UTC)
        block = await reporter.generate_top_leagues_block(
            start, end,
            alert_competition_ids=["11965"],
            on_time_threshold_seconds=300,
        )
        assert "EPL" in block
        assert "retention" not in block.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_market_reporter.py::TestTopLeaguesNoRetention -v`
Expected: FAIL — the current output includes `retention` in each league line.

- [ ] **Step 3: Remove retention from `generate_top_leagues_block`**

In `src/live_coverage_bot/core/market_reporter.py`, modify `generate_top_leagues_block()`:

1. Remove the `retention_map` building block (lines 177-184).
2. Remove the `retentions` computation and `retention_str` variable (lines 215-220).
3. Change the output line to exclude `| {retention_str} retention` (line 226-229).

The updated loop body becomes:

```python
        # Sort leagues by total events descending
        for (comp, country), events in sorted(
            leagues.items(), key=lambda x: len(x[1]), reverse=True
        ):
            total = len(events)
            monitored = [e for e in events if e.status != EventStatus.UNMONITORED]
            on_time = sum(
                1 for e in monitored
                if e.status == EventStatus.LIVE
                and (e.transition_delay_sec or 0) < on_time_threshold_seconds
            )
            late = sum(
                1 for e in monitored
                if e.status == EventStatus.LIVE
                and (e.transition_delay_sec or 0) >= on_time_threshold_seconds
            )
            never_live = sum(1 for e in monitored if e.status == EventStatus.NEVER_LIVE)

            delays = [
                e.transition_delay_sec
                for e in monitored
                if e.status == EventStatus.LIVE
                and (e.transition_delay_sec or 0) >= on_time_threshold_seconds
                and e.transition_delay_sec is not None
            ]
            avg_delay_str = f"{sum(delays) / len(delays) / 60:.1f}min" if delays else "n/a"

            on_time_pct = f"{on_time / len(monitored) * 100:.0f}%" if monitored else "0%"
            label = f"{comp} ({country})" if country else comp

            lines.append(
                f"  {label}: {total} events | {on_time_pct} on time | "
                f"{late} late, {never_live} never live | "
                f"avg delay {avg_delay_str}"
            )
```

Also remove the unused import of `self._markets` calls — the method no longer queries market data. The full method signature stays the same, but the body no longer references `self._markets`:

```python
    async def generate_top_leagues_block(
        self,
        start: datetime,
        end: datetime,
        alert_competition_ids: list[str],
        on_time_threshold_seconds: int = 300,
    ) -> str:
        """Return a per-league breakdown for top leagues in the weekly report."""
        if not alert_competition_ids:
            return ""

        all_events = await self._events.get_events_in_date_range(start, end)
        top_ids = set(alert_competition_ids)
        top_events = [e for e in all_events if e.competition_id in top_ids]

        if not top_events:
            return "\u2500\u2500\u2500 Top Leagues \u2500\u2500\u2500\n\nNo events for top leagues in this period."

        # Group events by (competition, country) for display
        leagues: dict[tuple[str, str], list[TrackedEvent]] = {}
        for e in top_events:
            key = (e.competition, e.country or "")
            leagues.setdefault(key, []).append(e)

        lines = ["\u2500\u2500\u2500 Top Leagues \u2500\u2500\u2500", ""]

        # Sort leagues by total events descending
        for (comp, country), events in sorted(
            leagues.items(), key=lambda x: len(x[1]), reverse=True
        ):
            # ... as shown above
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_market_reporter.py::TestTopLeaguesNoRetention -v`
Expected: PASS

- [ ] **Step 5: Run all market reporter tests**

Run: `pytest tests/test_market_reporter.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/core/market_reporter.py tests/test_market_reporter.py
git commit -m "feat: remove market retention from top leagues block"
```

---

### Task 6: Split `_check_weekly_report()` into two report flows

**Files:**
- Modify: `src/live_coverage_bot/core/loop.py:545-627`
- Test: `tests/test_loop.py`

- [ ] **Step 1: Write the failing test for split reports**

In `tests/test_loop.py`, add the following. First, check what imports and fixtures exist, then add:

```python
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import UTC, datetime
from pathlib import Path

from live_coverage_bot.config.models import (
    DatabaseConfig, MarketsConfig, PollingConfig, ReportingConfig,
    Settings, SlackConfig, ThresholdConfig,
)
from live_coverage_bot.core.loop import MonitoringLoop
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
from live_coverage_bot.db.repository import EventRepository


def _make_loop_settings(**markets_overrides) -> Settings:
    markets_kwargs = {"enabled": True}
    markets_kwargs.update(markets_overrides)
    return Settings(
        polling=PollingConfig(),
        thresholds=ThresholdConfig(),
        slack=SlackConfig(bot_token="xoxb-test", channel_id="C123"),
        database=DatabaseConfig(path=":memory:"),
        reporting=ReportingConfig(day="tuesday", time="08:00", week_starts="tuesday"),
        markets=MarketsConfig(**markets_kwargs),
        _env_file=None,
    )


class TestSplitWeeklyReport:
    async def test_posts_two_separate_messages(self, db):
        """Weekly report sends coverage message + market message as two separate posts."""
        settings = _make_loop_settings()
        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = EventRepository(db)
        loop._market_repo = MarketRepository(db)

        mock_slack = AsyncMock()
        mock_slack.post_summary = AsyncMock(side_effect=["ts_coverage", "ts_market"])
        mock_slack.upload_file = AsyncMock()
        mock_slack._config = settings.slack

        # Tuesday 08:01 UTC triggers the report
        now = datetime(2026, 4, 14, 8, 1, tzinfo=UTC)

        with patch("live_coverage_bot.core.loop.WeeklyReporter") as MockReporter, \
             patch("live_coverage_bot.core.loop.MarketReporter") as MockMarketReporter:

            mock_reporter_inst = AsyncMock()
            mock_reporter_inst.compute_report_period.return_value = (
                datetime(2026, 4, 7, tzinfo=UTC),
                datetime(2026, 4, 13, 23, 59, 59, tzinfo=UTC),
            )
            mock_reporter_inst.generate_slack_summary = AsyncMock(return_value="coverage summary")
            mock_reporter_inst.generate_csv = AsyncMock(return_value="csv,data\n1,2\n")
            MockReporter.return_value = mock_reporter_inst

            mock_market_inst = AsyncMock()
            mock_market_inst.generate_top_leagues_block = AsyncMock(return_value="top leagues block")
            mock_market_inst.generate_minimal_summary = AsyncMock(return_value="market summary")
            mock_market_inst.generate_aggregate_csv = AsyncMock(return_value="market,csv\na,b\n")
            mock_market_inst.generate_per_event_csv = AsyncMock(return_value="per,event\n")
            MockMarketReporter.return_value = mock_market_inst

            # Mock get_comparisons_in_date_range to return empty (no per-event CSVs)
            loop._market_repo.get_comparisons_in_date_range = AsyncMock(return_value=[])

            await loop._check_weekly_report(mock_slack, now)

        # Two separate post_summary calls
        assert mock_slack.post_summary.call_count == 2

        # First call: coverage summary + top leagues
        first_summary = mock_slack.post_summary.call_args_list[0][0][0]
        assert "coverage summary" in first_summary
        assert "top leagues block" in first_summary

        # Second call: minimal market summary
        second_summary = mock_slack.post_summary.call_args_list[1][0][0]
        assert "market summary" in second_summary

        # Two upload_file calls (one CSV each)
        assert mock_slack.upload_file.call_count == 2

    async def test_coverage_csv_threaded_to_coverage_message(self, db):
        """Coverage CSV is uploaded threaded to the coverage message ts."""
        settings = _make_loop_settings()
        loop = MonitoringLoop(settings)
        loop._db = db
        loop._repo = EventRepository(db)
        loop._market_repo = MarketRepository(db)

        mock_slack = AsyncMock()
        mock_slack.post_summary = AsyncMock(side_effect=["ts_cov", "ts_mkt"])
        mock_slack.upload_file = AsyncMock()
        mock_slack._config = settings.slack

        now = datetime(2026, 4, 14, 8, 1, tzinfo=UTC)

        with patch("live_coverage_bot.core.loop.WeeklyReporter") as MockReporter, \
             patch("live_coverage_bot.core.loop.MarketReporter") as MockMarketReporter:

            mock_reporter_inst = AsyncMock()
            mock_reporter_inst.compute_report_period.return_value = (
                datetime(2026, 4, 7, tzinfo=UTC),
                datetime(2026, 4, 13, 23, 59, 59, tzinfo=UTC),
            )
            mock_reporter_inst.generate_slack_summary = AsyncMock(return_value="cov")
            mock_reporter_inst.generate_csv = AsyncMock(return_value="csv_content")
            MockReporter.return_value = mock_reporter_inst

            mock_market_inst = AsyncMock()
            mock_market_inst.generate_top_leagues_block = AsyncMock(return_value="")
            mock_market_inst.generate_minimal_summary = AsyncMock(return_value="mkt")
            mock_market_inst.generate_aggregate_csv = AsyncMock(return_value="mkt_csv")
            mock_market_inst.generate_per_event_csv = AsyncMock(return_value="")
            MockMarketReporter.return_value = mock_market_inst

            loop._market_repo.get_comparisons_in_date_range = AsyncMock(return_value=[])

            await loop._check_weekly_report(mock_slack, now)

        # First upload_file call should use ts_cov as thread
        first_upload = mock_slack.upload_file.call_args_list[0]
        assert first_upload[1].get("thread_ts") == "ts_cov" or \
               (len(first_upload[0]) > 3 and first_upload[0][3] == "ts_cov")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_loop.py::TestSplitWeeklyReport -v`
Expected: FAIL — current `_check_weekly_report` sends one combined message and doesn't call `upload_file`.

- [ ] **Step 3: Rewrite `_check_weekly_report`**

Replace the body of `_check_weekly_report` in `src/live_coverage_bot/core/loop.py` (lines 545-627) with:

```python
    async def _check_weekly_report(self, slack: SlackClient, now: datetime) -> None:
        """Check if it's time to generate the weekly report."""
        assert self._repo is not None
        assert self._market_repo is not None
        cfg = self._settings.reporting
        target_weekday = WeeklyReporter.WEEKDAY_MAP.get(cfg.day.lower())
        if target_weekday is None:
            return

        if now.weekday() != target_weekday:
            return

        hour, minute = map(int, cfg.time.split(":"))
        if now.hour != hour or now.minute < minute:
            return

        if self._last_report_date and self._last_report_date.date() == now.date():
            return

        reporter = WeeklyReporter(
            self._repo,
            week_starts=cfg.week_starts,
            on_time_threshold_seconds=self._settings.thresholds.on_time_threshold_seconds,
        )
        market_reporter = MarketReporter(self._repo, self._market_repo)
        start, end = reporter.compute_report_period(now)
        summary_channel = slack._config.summary_channel_id or slack._config.channel_id
        date_str = now.strftime("%Y-%m-%d")

        try:
            # --- Message 1: Live Coverage Report ---
            summary = await reporter.generate_slack_summary(start, end)

            if self._settings.markets.enabled:
                top_leagues_block = await market_reporter.generate_top_leagues_block(
                    start, end,
                    alert_competition_ids=self._settings.markets.alert_competition_ids,
                    on_time_threshold_seconds=self._settings.thresholds.on_time_threshold_seconds,
                )
                if top_leagues_block:
                    summary = summary + "\n\n" + top_leagues_block

            coverage_ts = await slack.post_summary(summary)

            # Coverage CSV
            csv_content = await reporter.generate_csv(start, end)
            output_dir = Path(cfg.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            csv_path = output_dir / f"weekly-{date_str}.csv"
            csv_path.write_text(csv_content, encoding="utf-8")

            await slack.upload_file(
                content=csv_content,
                filename=f"weekly-{date_str}.csv",
                channel=summary_channel,
                thread_ts=coverage_ts,
            )

            # --- Message 2: Market Retention Report ---
            if self._settings.markets.enabled:
                retention_threshold = self._settings.markets.alert_thresholds.retention_below_pct
                market_summary = await market_reporter.generate_minimal_summary(
                    start, end, retention_threshold=retention_threshold,
                )
                market_ts = await slack.post_summary(market_summary)

                market_csv = await market_reporter.generate_aggregate_csv(start, end)
                market_csv_path = output_dir / f"weekly-markets-{date_str}.csv"
                market_csv_path.write_text(market_csv, encoding="utf-8")

                await slack.upload_file(
                    content=market_csv,
                    filename=f"weekly-markets-{date_str}.csv",
                    channel=summary_channel,
                    thread_ts=market_ts,
                )

                # Per-event CSVs (disk only)
                event_dir = output_dir / "events" / date_str
                event_dir.mkdir(parents=True, exist_ok=True)
                comparisons = await self._market_repo.get_comparisons_in_date_range(
                    start, end
                )
                for cmp in comparisons:
                    per_event_csv = await market_reporter.generate_per_event_csv(
                        cmp.event_id
                    )
                    event = await self._repo.get_by_id(cmp.event_id)
                    if event is None:
                        continue
                    safe_home = "".join(
                        c if c.isalnum() else "_" for c in event.home_team
                    )
                    safe_away = "".join(
                        c if c.isalnum() else "_" for c in event.away_team
                    )
                    filename = (
                        f"{event.betpawa_event_id}_{safe_home}_vs_{safe_away}.csv"
                    )
                    (event_dir / filename).write_text(per_event_csv, encoding="utf-8")

            self._last_report_date = now
            logger.info("Weekly report generated (coverage + market retention)")

        except Exception as e:
            logger.error("Failed to generate weekly report: %s", e)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_loop.py::TestSplitWeeklyReport -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest -v`
Expected: ALL PASS. If any existing tests fail due to the `post_summary` return type change (it now returns `str` instead of `None`), update those tests to handle the return value.

- [ ] **Step 6: Commit**

```bash
git add src/live_coverage_bot/core/loop.py tests/test_loop.py
git commit -m "feat: split weekly report into coverage + market retention messages with CSV uploads"
```

---

### Task 7: Final verification

**Files:** None (test-only)

- [ ] **Step 1: Run full test suite**

Run: `pytest -v`
Expected: ALL PASS

- [ ] **Step 2: Run linter**

Run: `ruff check src/ tests/`
Expected: No errors. Fix any issues found.

- [ ] **Step 3: Final commit (if any lint fixes)**

```bash
git add -u
git commit -m "fix: lint cleanup"
```
