"""Tests for Slack Web API client."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from live_coverage_bot.clients.slack import SlackClient, SlackError
from live_coverage_bot.config.models import SlackConfig
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.clients.models import ProviderID, ProviderType


@pytest.fixture
def slack_config():
    return SlackConfig(bot_token="xoxb-test-token", channel_id="C12345")


@pytest.fixture
def sample_event():
    return TrackedEvent(
        id=1,
        betpawa_event_id="99001",
        home_team="Arsenal",
        away_team="Chelsea",
        competition="England Premier League",
        country="England",
        scheduled_kickoff=datetime(2026, 4, 15, 15, 0, tzinfo=UTC),
        status=EventStatus.LATE,
        provider_ids=[ProviderID(type=ProviderType.SPORTRADAR, id="12345")],
        first_seen_prematch=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
    )


class TestSlackMessageFormatting:
    def test_format_late_message(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        now = datetime(2026, 4, 15, 15, 12, tzinfo=UTC)
        text = client.format_parent_message(sample_event, now)
        assert "LATE" in text
        assert "Arsenal vs Chelsea" in text
        assert "England Premier League" in text
        assert "SPORTRADAR" in text
        assert "12345" in text

    def test_format_went_live_message(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.LIVE
        sample_event.first_seen_live = datetime(2026, 4, 15, 15, 12, tzinfo=UTC)
        sample_event.transition_delay_sec = 720
        text = client.format_parent_message(sample_event)
        assert "WENT LIVE" in text
        assert "+12min" in text

    def test_format_never_live_message(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.NEVER_LIVE
        text = client.format_parent_message(sample_event)
        assert "NEVER LIVE" in text

    def test_format_thread_reply(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        now = datetime(2026, 4, 15, 15, 5, tzinfo=UTC)
        text = client.format_thread_reply(
            EventStatus.PREMATCH, EventStatus.LATE, now, "5min past kickoff"
        )
        assert "15:05" in text
        assert "5min past kickoff" in text


class TestSlackApiCalls:
    async def test_post_alert_returns_ts(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "ts": "1234567890.123456"}

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            ts = await client.post_alert(sample_event, datetime.now(tz=UTC))
            assert ts == "1234567890.123456"
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["channel"] == "C12345"

    async def test_post_alert_raises_on_not_ok(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": False, "error": "channel_not_found"}

        with patch.object(client._client, "post", return_value=mock_response):
            with pytest.raises(SlackError, match="channel_not_found"):
                await client.post_alert(sample_event, datetime.now(tz=UTC))

    async def test_update_message(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            await client.update_message("1234.5678", sample_event)
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["ts"] == "1234.5678"

    async def test_post_thread_reply(self, slack_config):
        client = SlackClient(slack_config)
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            await client.post_thread_reply("1234.5678", "test reply text")
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["thread_ts"] == "1234.5678"


class TestSlackMarketMessages:
    def test_format_market_recap(self, slack_config, sample_event):
        from live_coverage_bot.clients.slack import SlackClient
        from live_coverage_bot.models.markets import (
            ComparisonDetails,
            DroppedMarketDetail,
            MarketComparison,
            OddsShiftDetail,
            SnapshotPhase,
        )

        client = SlackClient(slack_config)
        now = datetime(2026, 4, 15, 15, 12, tzinfo=UTC)
        cmp = MarketComparison(
            event_id=1,
            compared_at=now,
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0,
            markets_dropped=53,
            markets_kept=34,
            retention_pct=39.1,
            dropped_key_markets=["Both Teams To Score - FT"],
            max_odds_shift_pct=16.7,
            triggered_alert=True,
            details=ComparisonDetails(
                dropped=[
                    DroppedMarketDetail(market_type_id="m1", market_type_name="Corner markets"),
                    DroppedMarketDetail(market_type_id="m2", market_type_name="Player specials"),
                ],
                added=[],
                odds_shifts=[
                    OddsShiftDetail(
                        market_type_name="1X2 - FT",
                        selection_name="1",
                        selection_type_id="3744",
                        handicap=None,
                        prematch_price=2.10,
                        live_price=2.45,
                        shift_pct=16.7,
                    ),
                ],
            ),
        )
        text = client.format_market_recap(cmp, now)
        assert "15:12" in text
        assert "Market comparison" in text
        assert "87" in text
        assert "34 live" in text
        assert "39" in text
        assert "Both Teams To Score - FT" in text
        assert "16.7" in text or "16" in text

    def test_format_market_anomaly_parent(self, slack_config, sample_event):
        from live_coverage_bot.clients.slack import SlackClient
        from live_coverage_bot.models.markets import (
            ComparisonDetails,
            MarketComparison,
            SnapshotPhase,
        )

        client = SlackClient(slack_config)
        cmp = MarketComparison(
            event_id=1,
            compared_at=datetime(2026, 4, 15, 15, 1, tzinfo=UTC),
            prematch_phase=SnapshotPhase.PREMATCH_1,
            markets_added=0, markets_dropped=50, markets_kept=20,
            retention_pct=28.6,
            dropped_key_markets=["1X2 - FT"],
            max_odds_shift_pct=5.0, triggered_alert=True,
            details=ComparisonDetails(dropped=[], added=[], odds_shifts=[]),
        )
        text = client.format_market_anomaly_parent(sample_event, cmp)
        assert "MARKET ANOMALY" in text
        assert "Arsenal vs Chelsea" in text
        assert "retention" in text
        assert "1X2 - FT" in text

    async def test_post_market_recap(self, slack_config):
        from live_coverage_bot.clients.slack import SlackClient

        client = SlackClient(slack_config)
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            await client.post_market_recap("1234.5678", "recap text")
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["thread_ts"] == "1234.5678"

    async def test_post_market_anomaly_alert_returns_ts(self, slack_config, sample_event):
        from live_coverage_bot.clients.slack import SlackClient

        client = SlackClient(slack_config)
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "ts": "9999.0000"}

        with patch.object(client._client, "post", return_value=mock_response):
            ts = await client.post_market_anomaly_alert(sample_event, "parent text")
            assert ts == "9999.0000"


class TestRemovedFormatting:
    def test_format_removed_message(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.REMOVED
        now = datetime(2026, 4, 15, 14, 0, tzinfo=UTC)
        text = client.format_parent_message(sample_event, now)
        assert "REMOVED" in text

    def test_format_removal_with_market_count(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.REMOVED
        sample_event.pre_removal_market_count = 56
        now = datetime(2026, 4, 15, 14, 0, tzinfo=UTC)
        text = client.format_parent_message(sample_event, now)
        assert "56 markets" in text

    def test_format_recovery_reply(self, slack_config):
        from live_coverage_bot.models.markets import MatchState

        client = SlackClient(slack_config)
        ms = MatchState(minute="1", period="First Half", home_score=0, away_score=0)
        text = client.format_recovery_reply(
            feed="live", gap_minutes=15,
            pre_removal_markets=56, current_markets=48,
            match_state=ms,
        )
        assert "Reappeared" in text
        assert "live" in text
        assert "56" in text
        assert "48" in text


class TestDetailedMarketThread:
    def test_format_missing_markets(self, slack_config):
        from live_coverage_bot.models.markets import (
            DroppedMarketDetail,
        )

        client = SlackClient(slack_config)
        dropped = [
            DroppedMarketDetail(market_type_id="1", market_type_name="1X2 - FT"),
            DroppedMarketDetail(market_type_id="2", market_type_name="Both Teams To Score - FT"),
            DroppedMarketDetail(market_type_id="3", market_type_name="Correct Score - FT"),
        ]
        text = client.format_missing_markets(dropped, ["1X2 - FT", "Both Teams To Score - FT"])
        assert "1X2 - FT" in text
        assert "KEY" in text
        assert "Correct Score - FT" in text

    def test_format_missing_markets_shows_all(self, slack_config):
        from live_coverage_bot.models.markets import DroppedMarketDetail

        client = SlackClient(slack_config)
        dropped = [
            DroppedMarketDetail(market_type_id=str(i), market_type_name=f"Market {i}")
            for i in range(12)
        ]
        text = client.format_missing_markets(dropped, ["Market 0"])
        assert "KEY" in text
        assert "Market 11" in text
        assert "more" not in text

    def test_format_odds_shifts(self, slack_config):
        from live_coverage_bot.models.markets import OddsShiftDetail

        client = SlackClient(slack_config)
        shifts = [
            OddsShiftDetail(market_type_name="1X2 - FT", selection_name="Home",
                selection_type_id="1", handicap=None,
                prematch_price=1.50, live_price=1.85, shift_pct=23.3),
            OddsShiftDetail(market_type_name="BTTS", selection_name="Yes",
                selection_type_id="3", handicap=None,
                prematch_price=1.75, live_price=1.60, shift_pct=8.6),
        ]
        text = client.format_odds_shifts(shifts, threshold=5.0)
        assert "1X2 - FT" in text
        assert "Home" in text
        assert "1.50" in text
        assert "1.85" in text
        assert "BTTS" in text

    def test_format_odds_shifts_below_threshold(self, slack_config):
        from live_coverage_bot.models.markets import OddsShiftDetail

        client = SlackClient(slack_config)
        shifts = [
            OddsShiftDetail(market_type_name="1X2 - FT", selection_name="Home",
                selection_type_id="1", handicap=None,
                prematch_price=2.00, live_price=2.01, shift_pct=0.5),
        ]
        text = client.format_odds_shifts(shifts, threshold=5.0)
        assert "No significant odds shifts" in text

    def test_format_snapshot_update(self, slack_config):
        from live_coverage_bot.models.markets import MatchState

        client = SlackClient(slack_config)
        ms = MatchState(minute="4", period="First Half", home_score=0, away_score=0)
        text = client.format_snapshot_update(
            match_state=ms, phase_label="+2min",
            prev_kept=22, curr_kept=31,
            prev_retention=39.0, curr_retention=55.0,
            recovered=["Double Chance - FT"], still_missing=["1X2 - FT"],
            key_markets=["1X2 - FT"],
        )
        assert "4'" in text
        assert "22" in text
        assert "31" in text
        assert "Recovered" in text
        assert "Still missing" in text
        assert "KEY" in text


class TestBotStatusFormatting:
    def test_format_shutdown_message(self, slack_config):
        client = SlackClient(slack_config)
        now = datetime(2026, 4, 20, 14, 32, tzinfo=UTC)
        msg = client.format_shutdown_message(now)
        assert "shutting down" in msg.lower()
        assert "14:32" in msg

    def test_format_crash_message(self, slack_config):
        client = SlackClient(slack_config)
        msg = client.format_crash_message("ValueError", "invalid literal")
        assert "crashed" in msg.lower()
        assert "ValueError" in msg
        assert "invalid literal" in msg

    def test_format_recovery_summary(self, slack_config):
        client = SlackClient(slack_config)
        downtime_start = datetime(2026, 4, 18, 14, 32, tzinfo=UTC)
        now = datetime(2026, 4, 20, 9, 15, tzinfo=UTC)
        msg = client.format_recovery_summary(
            downtime_start=downtime_start,
            now=now,
            unmonitored_count=12,
            active_remaining=3,
        )
        assert "restarting" in msg.lower() or "downtime" in msg.lower()
        assert "12" in msg
        assert "3" in msg
        assert "Apr 18" in msg or "18" in msg


class TestLateReasonFormatting:
    def test_went_live_coverage_late(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.LIVE
        sample_event.first_seen_live = datetime(2026, 4, 15, 15, 25, tzinfo=UTC)
        sample_event.transition_delay_sec = 1500
        sample_event.late_reason = "COVERAGE_LATE"
        sample_event.live_minute = 23
        text = client.format_parent_message(sample_event)
        assert "WENT LIVE" in text
        assert "23'" in text
        assert "coverage started late" in text.lower()

    def test_went_live_match_delayed(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.LIVE
        sample_event.first_seen_live = datetime(2026, 4, 15, 15, 12, tzinfo=UTC)
        sample_event.transition_delay_sec = 720
        sample_event.late_reason = "MATCH_DELAYED"
        sample_event.live_minute = 2
        text = client.format_parent_message(sample_event)
        assert "WENT LIVE" in text
        assert "2'" in text
        assert "started late" in text.lower()

    def test_went_live_no_late_reason(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.status = EventStatus.LIVE
        sample_event.first_seen_live = datetime(2026, 4, 15, 15, 1, tzinfo=UTC)
        sample_event.transition_delay_sec = 60
        sample_event.late_reason = None
        sample_event.live_minute = 1
        text = client.format_parent_message(sample_event)
        assert "WENT LIVE" in text
        assert "coverage started late" not in text.lower()

    def test_format_reschedule_message(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        old_kickoff = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        new_kickoff = datetime(2026, 4, 15, 13, 0, tzinfo=UTC)
        sample_event.scheduled_kickoff = new_kickoff
        text = client.format_reschedule_message(sample_event, old_kickoff, new_kickoff)
        assert "RESCHEDULED" in text
        assert "12:00" in text
        assert "13:00" in text
        assert "Arsenal vs Chelsea" in text


class TestEventHeaderHelper:
    def test_format_event_header(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        provider_str, competition_line, kickoff_str = client._format_event_header(sample_event)
        assert "SPORTRADAR #12345" in provider_str
        assert "England Premier League | England" == competition_line
        assert "15:00 UTC" == kickoff_str

    def test_format_event_header_no_country(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        sample_event.country = None
        provider_str, competition_line, kickoff_str = client._format_event_header(sample_event)
        assert competition_line == "England Premier League"
