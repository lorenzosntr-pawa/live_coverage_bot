"""Entry point for running Live Coverage Bot as a module."""

import asyncio
import logging
import sys

from live_coverage_bot.config import load_config
from live_coverage_bot.core import MonitoringLoop


def main() -> int:
    """Run the live coverage bot.

    Configures logging, loads settings, and runs the monitoring loop.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Suppress verbose httpx/httpcore request logging
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)

    try:
        settings = load_config()
    except Exception as e:
        logger.error("Failed to load settings: %s", e)
        return 1

    logger.info(
        "Starting Live Coverage Bot (polling every %ds)",
        settings.polling_interval_seconds,
    )

    loop = MonitoringLoop(settings)
    try:
        asyncio.run(loop.run())
    except KeyboardInterrupt:
        logger.info("Shutdown requested, exiting")

    return 0


if __name__ == "__main__":
    sys.exit(main())
