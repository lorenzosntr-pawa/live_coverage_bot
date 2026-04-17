"""Tests for BetPawa response parser functions."""

from datetime import UTC, datetime

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.clients.parsers import (
    extract_provider_ids,
    extract_scores,
    parse_event_markets,
    parse_live_event,
    parse_upcoming_event,
)
from live_coverage_bot.models.markets import Market, MarketRow, Selection


class TestExtractProviderIds:
    def test_extracts_sportradar(self):
        widgets = [{"type": "SPORTRADAR", "id": "12345"}]
        result = extract_provider_ids(widgets)
        assert len(result) == 1
        assert result[0].type == ProviderType.SPORTRADAR
        assert result[0].id == "12345"

    def test_extracts_geniussports(self):
        widgets = [{"type": "GENIUSSPORTS", "id": "67890"}]
        result = extract_provider_ids(widgets)
        assert len(result) == 1
        assert result[0].type == ProviderType.GENIUSSPORTS
        assert result[0].id == "67890"

    def test_extracts_both_types(self):
        widgets = [
            {"type": "SPORTRADAR", "id": "111"},
            {"type": "GENIUSSPORTS", "id": "222"},
        ]
        result = extract_provider_ids(widgets)
        assert len(result) == 2
        types = {p.type for p in result}
        assert ProviderType.SPORTRADAR in types
        assert ProviderType.GENIUSSPORTS in types

    def test_deduplication(self):
        widgets = [
            {"type": "SPORTRADAR", "id": "111"},
            {"type": "SPORTRADAR", "id": "111"},
        ]
        result = extract_provider_ids(widgets)
        assert len(result) == 1

    def test_ignores_unknown_type(self):
        widgets = [{"type": "UNKNOWN_PROVIDER", "id": "999"}]
        result = extract_provider_ids(widgets)
        assert result == []

    def test_ignores_missing_type(self):
        widgets = [{"id": "999"}]
        result = extract_provider_ids(widgets)
        assert result == []

    def test_ignores_missing_id(self):
        widgets = [{"type": "SPORTRADAR"}]
        result = extract_provider_ids(widgets)
        assert result == []

    def test_empty_widgets(self):
        result = extract_provider_ids([])
        assert result == []

    def test_mixed_valid_and_unknown(self):
        widgets = [
            {"type": "SPORTRADAR", "id": "111"},
            {"type": "BETGENIUS", "id": "999"},
            {"type": "GENIUSSPORTS", "id": "222"},
        ]
        result = extract_provider_ids(widgets)
        assert len(result) == 2
        ids = {p.id for p in result}
        assert ids == {"111", "222"}


class TestExtractScores:
    def _make_results(self, home_score: str, away_score: str) -> dict:
        return {
            "participantPeriodResults": [
                {
                    "participant": {"type": "HOME"},
                    "periodResults": [
                        {
                            "period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"},
                            "result": home_score,
                        }
                    ],
                },
                {
                    "participant": {"type": "AWAY"},
                    "periodResults": [
                        {
                            "period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"},
                            "result": away_score,
                        }
                    ],
                },
            ]
        }

    def test_extracts_full_time_scores(self):
        results = self._make_results("2", "1")
        home, away = extract_scores(results)
        assert home == 2
        assert away == 1

    def test_zero_scores(self):
        results = self._make_results("0", "0")
        home, away = extract_scores(results)
        assert home == 0
        assert away == 0

    def test_empty_results(self):
        home, away = extract_scores({})
        assert home is None
        assert away is None

    def test_invalid_score_returns_none(self):
        results = self._make_results("N/A", "1")
        home, away = extract_scores(results)
        assert home is None
        assert away == 1

    def test_ignores_non_full_time_period(self):
        results = {
            "participantPeriodResults": [
                {
                    "participant": {"type": "HOME"},
                    "periodResults": [
                        {
                            "period": {"slug": "FIRST_HALF"},
                            "result": "1",
                        }
                    ],
                }
            ]
        }
        home, away = extract_scores(results)
        assert home is None
        assert away is None

    def test_empty_participant_results(self):
        results = {"participantPeriodResults": []}
        home, away = extract_scores(results)
        assert home is None
        assert away is None


class TestParseLiveEvent:
    def _make_event_data(self, **overrides) -> dict:
        data = {
            "id": "12345",
            "startTime": "2026-04-17T15:00:00Z",
            "participants": [
                {"position": 1, "name": "Arsenal"},
                {"position": 2, "name": "Chelsea"},
            ],
            "competition": {"id": "101", "name": "Premier League"},
            "region": {"name": "England"},
            "results": {},
            "widgets": [],
        }
        data.update(overrides)
        return data

    def test_minimal_event_parses(self):
        data = self._make_event_data()
        event = parse_live_event(data)
        assert event is not None
        assert event.event_id == "bp:12345"
        assert event.home_team == "Arsenal"
        assert event.away_team == "Chelsea"

    def test_prefixes_event_id_with_bp(self):
        data = self._make_event_data(id="99001")
        event = parse_live_event(data)
        assert event is not None
        assert event.event_id == "bp:99001"

    def test_returns_none_for_missing_id(self):
        data = self._make_event_data()
        data["id"] = ""
        result = parse_live_event(data)
        assert result is None

    def test_returns_none_for_no_id_key(self):
        data = self._make_event_data()
        del data["id"]
        result = parse_live_event(data)
        assert result is None

    def test_extracts_competition_info(self):
        data = self._make_event_data()
        event = parse_live_event(data)
        assert event is not None
        assert event.competition_id == "101"
        assert event.competition_name == "Premier League"

    def test_extracts_country_name(self):
        data = self._make_event_data()
        event = parse_live_event(data)
        assert event is not None
        assert event.country_name == "England"

    def test_null_region_yields_none_country(self):
        data = self._make_event_data(region=None)
        event = parse_live_event(data)
        assert event is not None
        assert event.country_name is None

    def test_parses_start_time(self):
        data = self._make_event_data(startTime="2026-04-17T15:00:00Z")
        event = parse_live_event(data)
        assert event is not None
        assert event.start_time == datetime(2026, 4, 17, 15, 0, 0, tzinfo=UTC)

    def test_invalid_start_time_uses_now(self):
        data = self._make_event_data(startTime="not-a-date")
        before = datetime.now(tz=UTC)
        event = parse_live_event(data)
        after = datetime.now(tz=UTC)
        assert event is not None
        assert before <= event.start_time <= after

    def test_provider_ids_set_when_widgets_present(self):
        data = self._make_event_data(widgets=[{"type": "SPORTRADAR", "id": "777"}])
        event = parse_live_event(data)
        assert event is not None
        assert len(event.provider_ids) == 1
        assert event.provider_ids[0].id == "777"

    def test_no_provider_ids_when_no_widgets(self):
        data = self._make_event_data(widgets=[])
        event = parse_live_event(data)
        assert event is not None
        # Falls through to SportyBet extraction, which returns [] for bp: prefix
        assert event._provider_ids_override is None

    def test_extracts_scores(self):
        data = self._make_event_data(results={
            "participantPeriodResults": [
                {
                    "participant": {"type": "HOME"},
                    "periodResults": [
                        {"period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"}, "result": "2"}
                    ],
                },
                {
                    "participant": {"type": "AWAY"},
                    "periodResults": [
                        {"period": {"slug": "FULL_TIME_EXCLUDING_OVERTIME"}, "result": "1"}
                    ],
                },
            ]
        })
        event = parse_live_event(data)
        assert event is not None
        assert event.home_score == 2
        assert event.away_score == 1


class TestParseUpcomingEvent:
    def _make_event_data(self, **overrides) -> dict:
        data = {
            "id": "55001",
            "startTime": "2026-04-17T18:30:00Z",
            "participants": [
                {"position": 1, "name": "Liverpool"},
                {"position": 2, "name": "ManCity"},
            ],
            "competition": {"name": "Premier League"},
            "region": {"name": "England"},
            "widgets": [],
        }
        data.update(overrides)
        return data

    def test_parses_correctly(self):
        data = self._make_event_data()
        event = parse_upcoming_event(data)
        assert event is not None
        assert event.event_id == "55001"
        assert event.home_team == "Liverpool"
        assert event.away_team == "ManCity"
        assert event.competition_name == "Premier League"
        assert event.country_name == "England"
        assert event.start_time == datetime(2026, 4, 17, 18, 30, 0, tzinfo=UTC)

    def test_returns_none_for_missing_id(self):
        data = self._make_event_data()
        data["id"] = ""
        assert parse_upcoming_event(data) is None

    def test_returns_none_for_missing_start_time(self):
        data = self._make_event_data()
        data["startTime"] = ""
        assert parse_upcoming_event(data) is None

    def test_returns_none_for_invalid_start_time(self):
        data = self._make_event_data(startTime="not-a-date")
        assert parse_upcoming_event(data) is None

    def test_includes_provider_ids(self):
        data = self._make_event_data(widgets=[
            {"type": "SPORTRADAR", "id": "111"},
            {"type": "GENIUSSPORTS", "id": "222"},
        ])
        event = parse_upcoming_event(data)
        assert event is not None
        assert len(event.provider_ids) == 2

    def test_null_region_yields_none_country(self):
        data = self._make_event_data(region=None)
        event = parse_upcoming_event(data)
        assert event is not None
        assert event.country_name is None

    def test_event_id_cast_to_str(self):
        data = self._make_event_data(id=12345)
        event = parse_upcoming_event(data)
        assert event is not None
        assert event.event_id == "12345"

    def test_no_id_key_returns_none(self):
        data = self._make_event_data()
        del data["id"]
        assert parse_upcoming_event(data) is None


class TestParseEventMarkets:
    def _make_market_data(self) -> dict:
        return {
            "markets": [
                {
                    "marketType": {
                        "id": "3743",
                        "name": "1X2",
                        "priority": 1,
                    },
                    "row": [
                        {
                            "id": "row1",
                            "handicap": None,
                            "prices": [
                                {
                                    "id": "p1",
                                    "typeId": "t1",
                                    "name": "1",
                                    "price": 2.5,
                                    "suspended": False,
                                },
                                {
                                    "id": "p2",
                                    "typeId": "t2",
                                    "name": "X",
                                    "price": 3.1,
                                    "suspended": False,
                                },
                                {
                                    "id": "p3",
                                    "typeId": "t3",
                                    "name": "2",
                                    "price": 2.9,
                                    "suspended": True,
                                },
                            ],
                        }
                    ],
                }
            ]
        }

    def test_parses_market(self):
        data = self._make_market_data()
        markets = parse_event_markets(data)
        assert len(markets) == 1
        assert isinstance(markets[0], Market)
        assert markets[0].market_type_id == "3743"
        assert markets[0].market_type_name == "1X2"
        assert markets[0].priority == 1

    def test_parses_rows(self):
        data = self._make_market_data()
        markets = parse_event_markets(data)
        rows = markets[0].rows
        assert len(rows) == 1
        assert isinstance(rows[0], MarketRow)
        assert rows[0].row_id == "row1"
        assert rows[0].handicap is None

    def test_parses_selections(self):
        data = self._make_market_data()
        markets = parse_event_markets(data)
        selections = markets[0].rows[0].selections
        assert len(selections) == 3
        names = {s.name for s in selections}
        assert names == {"1", "X", "2"}
        suspended = [s for s in selections if s.suspended]
        assert len(suspended) == 1
        assert suspended[0].name == "2"

    def test_empty_markets(self):
        result = parse_event_markets({"markets": []})
        assert result == []

    def test_no_markets_key(self):
        result = parse_event_markets({})
        assert result == []

    def test_skips_market_without_type_id(self):
        data = {
            "markets": [
                {"marketType": {"id": "", "name": "Bad"}, "row": []},
                {"marketType": {"id": "3743", "name": "1X2", "priority": 1}, "row": []},
            ]
        }
        result = parse_event_markets(data)
        assert len(result) == 1
        assert result[0].market_type_id == "3743"

    def test_skips_row_without_id(self):
        data = {
            "markets": [
                {
                    "marketType": {"id": "3743", "name": "1X2", "priority": 1},
                    "row": [
                        {"id": "", "handicap": None, "prices": []},
                        {"id": "row1", "handicap": None, "prices": []},
                    ],
                }
            ]
        }
        result = parse_event_markets(data)
        assert len(result[0].rows) == 1
        assert result[0].rows[0].row_id == "row1"

    def test_skips_selection_with_missing_price(self):
        data = {
            "markets": [
                {
                    "marketType": {"id": "3743", "name": "1X2", "priority": 1},
                    "row": [
                        {
                            "id": "row1",
                            "handicap": None,
                            "prices": [
                                {"id": "p1", "typeId": "t1", "name": "1", "price": None},
                                {"id": "p2", "typeId": "t2", "name": "X", "price": 3.1},
                            ],
                        }
                    ],
                }
            ]
        }
        result = parse_event_markets(data)
        assert len(result[0].rows[0].selections) == 1
        assert result[0].rows[0].selections[0].name == "X"

    def test_handicap_converted_to_str(self):
        data = {
            "markets": [
                {
                    "marketType": {"id": "100", "name": "AH", "priority": 2},
                    "row": [
                        {"id": "row1", "handicap": -1.5, "prices": []},
                    ],
                }
            ]
        }
        result = parse_event_markets(data)
        assert result[0].rows[0].handicap == "-1.5"
