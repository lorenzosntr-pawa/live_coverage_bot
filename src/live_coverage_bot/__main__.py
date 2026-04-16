"""Entry point for running Live Coverage Bot as a module."""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from live_coverage_bot.config import load_config
from live_coverage_bot.core.loop import MonitoringLoop
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository


def main() -> int:
    """Run the live coverage bot."""
    parser = argparse.ArgumentParser(description="BetPawa Prematch-to-Live Monitor")
    parser.add_argument(
        "--generate-report",
        action="store_true",
        help="Generate weekly report and exit",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)

    try:
        settings = load_config(args.config)
    except Exception as e:
        logger.error("Failed to load settings: %s", e)
        return 1

    if args.generate_report:
        return asyncio.run(_generate_report(settings, logger))

    logger.info(
        "Starting BetPawa Prematch-to-Live Monitor v2 (live: %ds, prematch: %ds)",
        settings.polling.live_interval_seconds,
        settings.polling.prematch_interval_seconds,
    )

    loop = MonitoringLoop(settings)
    try:
        asyncio.run(loop.run())
    except KeyboardInterrupt:
        logger.info("Shutdown requested, exiting")

    return 0


async def _generate_report(settings, logger) -> int:
    """Generate a weekly report on demand."""
    db = Database(settings.database.path)
    await db.initialize()
    repo = EventRepository(db)
    reporter = WeeklyReporter(repo, week_starts=settings.reporting.week_starts)

    now = datetime.now(tz=UTC)
    start, end = reporter.compute_report_period(now)

    summary = await reporter.generate_slack_summary(start, end)
    print(summary)

    csv_content = await reporter.generate_csv(start, end)
    output_dir = Path(settings.reporting.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"weekly-{now.strftime('%Y-%m-%d')}.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    logger.info("CSV report written to %s", csv_path)

    await db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
