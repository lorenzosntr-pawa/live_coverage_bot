"""Configuration schema models for Live Coverage Bot."""

from pydantic import BaseModel, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class SportyBetConfig(BaseModel):
    """SportyBet API configuration."""

    base_url: str = "https://www.sportybet.com/api/ng/factsCenter"


class BetPawaConfig(BaseModel):
    """BetPawa API configuration with required headers."""

    base_url: str = "https://www.betpawa.ng/api/sportsbook/v3"
    brand: str = "betpawa-nigeria"
    language: str = "en"
    device_type: str = "web"


class SlackConfig(BaseModel):
    """Slack notification configuration."""

    webhook_url: HttpUrl


class Settings(BaseSettings):
    """Application settings with environment variable support.

    Environment variables use LCB_ prefix and __ for nested values.
    Example: LCB_POLLING_INTERVAL_SECONDS=60, LCB_SLACK__WEBHOOK_URL=...
    """

    polling_interval_seconds: int = 30
    priority_leagues: list[str] = []  # Competition IDs to monitor
    sportybet: SportyBetConfig = SportyBetConfig()
    betpawa: BetPawaConfig = BetPawaConfig()
    slack: SlackConfig

    model_config = SettingsConfigDict(
        env_prefix="LCB_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )
