"""Entry point for running Live Coverage Bot as a module."""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from live_coverage_bot.clients.slack import SlackClient
from live_coverage_bot.config import load_config
from live_coverage_bot.core.loop import MonitoringLoop
from live_coverage_bot.core.market_reporter import MarketReporter
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.market_repository import MarketRepository
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
        "--send-report",
        action="store_true",
        help="Generate and send weekly report to Slack (with CSV), then exit",
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

    if args.send_report:
        return asyncio.run(_send_report(settings, logger))

    logger.info(
        "Starting BetPawa Prematch-to-Live Monitor v2 (live: %ds, prematch: %ds)",
        settings.polling.live_interval_seconds,
        settings.polling.prematch_interval_seconds,
    )

    loop = MonitoringLoop(settings)
    try:
        asyncio.run(loop.run())
    except KeyboardInterrupt:
        logger.info("Shutdown requested, posting notification")
        try:
            asyncio.run(_post_shutdown_notification(settings))
        except Exception as e:
            logger.warning("Failed to send shutdown notification: %s", e)
        logger.info("Exiting")

    return 0


async def _generate_report(settings, logger) -> int:
    """Generate a weekly report on demand."""
    db = Database(settings.database.path)
    await db.initialize()
    repo = EventRepository(db)
    market_repo = MarketRepository(db)
    reporter = WeeklyReporter(repo, week_starts=settings.reporting.week_starts, on_time_threshold_seconds=settings.thresholds.on_time_threshold_seconds)
    market_reporter = MarketReporter(repo, market_repo)

    now = datetime.now(tz=UTC)
    start, end = reporter.compute_report_period(now)

    summary = await reporter.generate_slack_summary(start, end)
    if settings.markets.enabled:
        top_leagues_block = await market_reporter.generate_top_leagues_block(
            start, end,
            alert_competition_ids=settings.markets.alert_competition_ids,
            on_time_threshold_seconds=settings.thresholds.on_time_threshold_seconds,
        )
        if top_leagues_block:
            summary = summary + "\n\n" + top_leagues_block
        markets_block = await market_reporter.generate_markets_summary_block(start, end)
        summary = summary + "\n\n" + markets_block
    print(summary)

    output_dir = Path(settings.reporting.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_content = await reporter.generate_csv(start, end)
    csv_path = output_dir / f"weekly-{now.strftime('%Y-%m-%d')}.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    logger.info("Lifecycle CSV written to %s", csv_path)

    if settings.markets.enabled:
        market_csv = await market_reporter.generate_aggregate_csv(start, end)
        market_csv_path = output_dir / f"weekly-markets-{now.strftime('%Y-%m-%d')}.csv"
        market_csv_path.write_text(market_csv, encoding="utf-8")
        logger.info("Markets CSV written to %s", market_csv_path)

        event_dir = output_dir / "events" / now.strftime("%Y-%m-%d")
        event_dir.mkdir(parents=True, exist_ok=True)
        comparisons = await market_repo.get_comparisons_in_date_range(start, end)
        for cmp in comparisons:
            per_event_csv = await market_reporter.generate_per_event_csv(cmp.event_id)
            event = await repo.get_by_id(cmp.event_id)
            if event is None:
                continue
            safe_home = "".join(c if c.isalnum() else "_" for c in event.home_team)
            safe_away = "".join(c if c.isalnum() else "_" for c in event.away_team)
            filename = f"{event.betpawa_event_id}_{safe_home}_vs_{safe_away}.csv"
            (event_dir / filename).write_text(per_event_csv, encoding="utf-8")
        logger.info("Per-event CSVs written to %s", event_dir)

    await db.close()
    return 0


async def _send_report(settings, logger) -> int:
    """Generate and send weekly report to Slack with CSV uploads."""
    db = Database(settings.database.path)
    await db.initialize()
    repo = EventRepository(db)
    market_repo = MarketRepository(db)
    reporter = WeeklyReporter(repo, week_starts=settings.reporting.week_starts, on_time_threshold_seconds=settings.thresholds.on_time_threshold_seconds)
    market_reporter = MarketReporter(repo, market_repo)

    now = datetime.now(tz=UTC)
    start, end = reporter.compute_report_period(now)
    date_str = now.strftime("%Y-%m-%d")

    summary = await reporter.generate_slack_summary(start, end)
    if settings.markets.enabled:
        top_leagues_block = await market_reporter.generate_top_leagues_block(
            start, end,
            alert_competition_ids=settings.markets.alert_competition_ids,
            on_time_threshold_seconds=settings.thresholds.on_time_threshold_seconds,
        )
        if top_leagues_block:
            summary = summary + "\n\n" + top_leagues_block

    async with SlackClient(settings.slack) as slack:
        summary_channel = settings.slack.summary_channel_id or settings.slack.channel_id

        # Post coverage report
        coverage_ts = await slack.post_summary(summary)
        logger.info("Coverage report posted to Slack")

        # Generate and upload CSV
        csv_content = await reporter.generate_csv(start, end)
        output_dir = Path(settings.reporting.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        csv_path = output_dir / f"weekly-{date_str}.csv"
        csv_path.write_text(csv_content, encoding="utf-8")

        await slack.upload_file(
            content=csv_content,
            filename=f"weekly-{date_str}.csv",
            channel=summary_channel,
            thread_ts=coverage_ts,
        )
        logger.info("Coverage CSV uploaded to thread")

        # Market retention report
        if settings.markets.enabled:
            retention_threshold = settings.markets.alert_thresholds.retention_below_pct
            market_summary = await market_reporter.generate_minimal_summary(
                start, end, retention_threshold=retention_threshold,
            )
            market_ts = await slack.post_summary(market_summary)
            logger.info("Market retention report posted to Slack")

            market_csv = await market_reporter.generate_aggregate_csv(start, end)
            market_csv_path = output_dir / f"weekly-markets-{date_str}.csv"
            market_csv_path.write_text(market_csv, encoding="utf-8")

            await slack.upload_file(
                content=market_csv,
                filename=f"weekly-markets-{date_str}.csv",
                channel=summary_channel,
                thread_ts=market_ts,
            )
            logger.info("Market CSV uploaded to thread")

    await db.close()
    logger.info("Weekly report sent successfully")
    return 0


async def _post_shutdown_notification(settings) -> None:
    """Post a shutdown notification to Slack."""
    async with SlackClient(settings.slack) as slack:
        now = datetime.now(tz=UTC)
        msg = slack.format_shutdown_message(now)
        await slack.post_bot_status(msg)


if __name__ == "__main__":
    sys.exit(main())
