"""Tests for configuration models."""

from live_coverage_bot.config.models import (
    BetPawaConfig,
    DatabaseConfig,
    PollingConfig,
    ReportingConfig,
    Settings,
    SlackConfig,
    ThresholdConfig,
)


class TestPollingConfig:
    def test_defaults(self):
        config = PollingConfig()
        assert config.live_interval_seconds == 30
        assert config.prematch_interval_seconds == 150
        assert config.prematch_lookahead_hours == 3


class TestThresholdConfig:
    def test_defaults(self):
        config = ThresholdConfig()
        assert config.grace_period_minutes == 5
        assert config.hard_timeout_minutes == 90


class TestDatabaseConfig:
    def test_defaults(self):
        config = DatabaseConfig()
        assert config.path == "data/events.db"
        assert config.retention_days == 30


class TestReportingConfig:
    def test_defaults(self):
        config = ReportingConfig()
        assert config.day == "tuesday"
        assert config.time == "08:00"
        assert config.week_starts == "tuesday"
        assert config.output_dir == "reports/"


class TestSlackConfig:
    def test_requires_bot_token(self):
        config = SlackConfig(bot_token="xoxb-test", channel_id="C123")
        assert config.bot_token == "xoxb-test"
        assert config.channel_id == "C123"
        assert config.summary_channel_id is None

    def test_summary_channel_defaults_to_none(self):
        config = SlackConfig(bot_token="xoxb-test", channel_id="C123")
        assert config.summary_channel_id is None


class TestSettings:
    def test_loads_with_minimal_config(self):
        settings = Settings(
            slack=SlackConfig(bot_token="xoxb-test", channel_id="C123"),
            _env_file=None,
        )
        assert settings.polling.live_interval_seconds == 30
        assert settings.thresholds.grace_period_minutes == 5
        assert settings.database.path == "data/events.db"
        assert settings.betpawa.brand == "betpawa-nigeria"


class TestMarketsConfig:
    def test_defaults(self):
        from live_coverage_bot.config.models import (
            MarketAlertThresholdsConfig,
            MarketSnapshotWindowsConfig,
            MarketsConfig,
        )

        config = MarketsConfig()
        assert config.enabled is True
        assert isinstance(config.snapshot_windows, MarketSnapshotWindowsConfig)
        assert config.snapshot_windows.prematch_60_min_before == [60, 55]
        assert config.snapshot_windows.prematch_15_min_before == [15, 10]
        assert config.snapshot_windows.prematch_1_min_before == [1, 0]
        assert isinstance(config.alert_thresholds, MarketAlertThresholdsConfig)
        assert config.alert_thresholds.retention_below_pct == 50
        assert config.alert_thresholds.any_key_market_dropped is True
        assert config.alert_thresholds.max_odds_shift_pct == 30
        assert "1X2 - FT" in config.key_markets
        assert "Total Score Over/Under - FT" in config.key_markets

    def test_settings_includes_markets_section(self):
        from live_coverage_bot.config.models import Settings, SlackConfig

        settings = Settings(
            slack=SlackConfig(bot_token="xoxb-test", channel_id="C123"),
            _env_file=None,
        )
        assert settings.markets.enabled is True
        assert settings.markets.alert_thresholds.retention_below_pct == 50

    def test_alert_competition_ids_defaults_to_empty(self):
        from live_coverage_bot.config.models import MarketsConfig

        config = MarketsConfig()
        assert config.alert_competition_ids == []

    def test_alert_competition_ids_accepts_list(self):
        from live_coverage_bot.config.models import MarketsConfig

        config = MarketsConfig(alert_competition_ids=["11965", "12097"])
        assert config.alert_competition_ids == ["11965", "12097"]


class TestNewMarketConfig:
    def test_default_live_snapshot_offsets(self):
        from live_coverage_bot.config.models import MarketsConfig
        cfg = MarketsConfig()
        assert cfg.live_snapshot_offsets_minutes == [0, 2, 5]

    def test_default_odds_shift_display_threshold(self):
        from live_coverage_bot.config.models import MarketsConfig
        cfg = MarketsConfig()
        assert cfg.odds_shift_display_threshold == 5.0
