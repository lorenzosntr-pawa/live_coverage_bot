"""Tests for Slack Web API client."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

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
        mock_response = AsyncMock()
        mock_response.json.return_value = {"ok": True, "ts": "1234567890.123456"}
        mock_response.raise_for_status = AsyncMock()

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            ts = await client.post_alert(sample_event, datetime.now(tz=UTC))
            assert ts == "1234567890.123456"
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["channel"] == "C12345"

    async def test_post_alert_raises_on_not_ok(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        mock_response = AsyncMock()
        mock_response.json.return_value = {"ok": False, "error": "channel_not_found"}
        mock_response.raise_for_status = AsyncMock()

        with patch.object(client._client, "post", return_value=mock_response):
            with pytest.raises(SlackError, match="channel_not_found"):
                await client.post_alert(sample_event, datetime.now(tz=UTC))

    async def test_update_message(self, slack_config, sample_event):
        client = SlackClient(slack_config)
        mock_response = AsyncMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status = AsyncMock()

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            await client.update_message("1234.5678", sample_event)
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["ts"] == "1234.5678"

    async def test_post_thread_reply(self, slack_config):
        client = SlackClient(slack_config)
        mock_response = AsyncMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status = AsyncMock()

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
        assert "28" in text
        assert "1X2 - FT" in text

    async def test_post_market_recap(self, slack_config):
        from live_coverage_bot.clients.slack import SlackClient

        client = SlackClient(slack_config)
        mock_response = AsyncMock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status = AsyncMock()

        with patch.object(client._client, "post", return_value=mock_response) as mock_post:
            await client.post_market_recap("1234.5678", "recap text")
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["thread_ts"] == "1234.5678"

    async def test_post_market_anomaly_alert_returns_ts(self, slack_config, sample_event):
        from live_coverage_bot.clients.slack import SlackClient

        client = SlackClient(slack_config)
        mock_response = AsyncMock()
        mock_response.json.return_value = {"ok": True, "ts": "9999.0000"}
        mock_response.raise_for_status = AsyncMock()

        with patch.object(client._client, "post", return_value=mock_response):
            ts = await client.post_market_anomaly_alert(sample_event, "parent text")
            assert ts == "9999.0000"


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
