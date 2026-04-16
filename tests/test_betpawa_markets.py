"""Tests for BetPawa event detail fetch with markets."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from live_coverage_bot.clients.betpawa import BetPawaClient, BetPawaError
from live_coverage_bot.config.models import BetPawaConfig
from live_coverage_bot.models.markets import Market


FIXTURE_DIR = Path(__file__).parent.parent / "examples_api_markets"


@pytest.fixture
def betpawa_client():
    return BetPawaClient(BetPawaConfig())


@pytest.fixture
def prematch_response_data():
    with open(FIXTURE_DIR / "prematch_response_34210635.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def live_response_data():
    with open(FIXTURE_DIR / "live_response_34397693.json", encoding="utf-8") as f:
        return json.load(f)


class TestGetEventMarkets:
    async def test_parses_prematch_response(self, betpawa_client, prematch_response_data):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=prematch_response_data)

        with pytest.MonkeyPatch().context() as mp:
            async def fake_get(*args, **kwargs):
                return mock_resp

            mp.setattr(betpawa_client._client, "get", fake_get)
            markets = await betpawa_client.get_event_markets("34210635")

        assert isinstance(markets, list)
        assert len(markets) > 0
        assert all(isinstance(m, Market) for m in markets)
        mt_ids = {m.market_type_id for m in markets}
        assert "3743" in mt_ids
        ft_market = next(m for m in markets if m.market_type_id == "3743")
        assert len(ft_market.rows) == 1
        assert len(ft_market.rows[0].selections) == 3
        selection_names = {s.name for s in ft_market.rows[0].selections}
        assert selection_names == {"1", "X", "2"}

    async def test_parses_live_response(self, betpawa_client, live_response_data):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=live_response_data)

        with pytest.MonkeyPatch().context() as mp:
            async def fake_get(*args, **kwargs):
                return mock_resp

            mp.setattr(betpawa_client._client, "get", fake_get)
            markets = await betpawa_client.get_event_markets("34397693")

        assert len(markets) > 0
        mt_ids = {m.market_type_id for m in markets}
        assert "3743" in mt_ids

    async def test_raises_on_http_error(self, betpawa_client):
        import httpx
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "not found", request=MagicMock(), response=MagicMock(status_code=404)
            )
        )
        mock_resp.response = MagicMock(status_code=404)

        with pytest.MonkeyPatch().context() as mp:
            async def fake_get(*args, **kwargs):
                return mock_resp

            mp.setattr(betpawa_client._client, "get", fake_get)
            with pytest.raises(BetPawaError):
                await betpawa_client.get_event_markets("bad")
