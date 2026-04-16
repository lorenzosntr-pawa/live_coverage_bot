"""Configuration schema models for Live Coverage Bot v2."""

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class PollingConfig(BaseModel):
    """Polling interval configuration."""

    live_interval_seconds: int = 30
    prematch_interval_seconds: int = 150
    prematch_lookahead_hours: int = 3


class ThresholdConfig(BaseModel):
    """Event lifecycle threshold configuration."""

    grace_period_minutes: int = 5
    hard_timeout_minutes: int = 90


class BetPawaConfig(BaseModel):
    """BetPawa API configuration with required headers."""

    base_url: str = "https://www.betpawa.ng/api/sportsbook/v3"
    brand: str = "betpawa-nigeria"
    language: str = "en"
    device_type: str = "web"


class SlackConfig(BaseModel):
    """Slack Web API configuration."""

    bot_token: str
    channel_id: str
    summary_channel_id: str | None = None


class DatabaseConfig(BaseModel):
    """SQLite database configuration."""

    path: str = "data/events.db"
    retention_days: int = 30


class ReportingConfig(BaseModel):
    """Weekly report configuration."""

    day: str = "tuesday"
    time: str = "08:00"
    week_starts: str = "tuesday"
    output_dir: str = "reports/"


class MarketSnapshotWindowsConfig(BaseModel):
    """Snapshot window minute ranges (relative to kickoff)."""

    prematch_60_min_before: list[int] = [60, 55]
    prematch_15_min_before: list[int] = [15, 10]
    prematch_1_min_before: list[int] = [1, 0]


class MarketAlertThresholdsConfig(BaseModel):
    """Thresholds for firing market comparison alerts."""

    retention_below_pct: float = 50
    any_key_market_dropped: bool = True
    max_odds_shift_pct: float = 30


class MarketsConfig(BaseModel):
    """Market comparison feature configuration."""

    enabled: bool = True
    snapshot_windows: MarketSnapshotWindowsConfig = MarketSnapshotWindowsConfig()
    alert_thresholds: MarketAlertThresholdsConfig = MarketAlertThresholdsConfig()
    key_markets: list[str] = [
        "1X2 - FT",
        "Total Score Over/Under - FT",
        "Both Teams To Score - FT",
        "Double Chance - FT",
        "1X2 - 1H",
    ]


class Settings(BaseSettings):
    """Application settings with environment variable support.

    Environment variables use LCB_ prefix and __ for nested values.
    Example: LCB_SLACK__BOT_TOKEN=xoxb-..., LCB_SLACK__CHANNEL_ID=C123
    """

    polling: PollingConfig = PollingConfig()
    thresholds: ThresholdConfig = ThresholdConfig()
    betpawa: BetPawaConfig = BetPawaConfig()
    slack: SlackConfig
    database: DatabaseConfig = DatabaseConfig()
    reporting: ReportingConfig = ReportingConfig()
    markets: MarketsConfig = MarketsConfig()

    model_config = SettingsConfigDict(
        env_prefix="LCB_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )
