"""Configuration loader for Live Coverage Bot."""

from pathlib import Path
from typing import Any

import yaml

from .models import Settings


def load_config(path: Path | None = None) -> Settings:
    """Load configuration from YAML file and environment variables.

    Args:
        path: Path to YAML config file. Defaults to config.yaml in current directory.

    Returns:
        Validated Settings instance.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValidationError: If required fields are missing (e.g., slack.webhook_url).

    Environment variables take precedence over YAML values.
    Use LCB_ prefix and __ for nested values.
    Example: LCB_SLACK__WEBHOOK_URL=https://hooks.slack.com/...
    """
    if path is None:
        path = Path("config.yaml")

    config_data: dict[str, Any] = {}

    if path.exists():
        with open(path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if loaded is not None:
                config_data = loaded

    # Settings will merge with environment variables automatically
    return Settings(**config_data)
