"""Slack Web API client for threaded event alerts."""

import logging
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

import httpx

from live_coverage_bot.config.models import SlackConfig
from live_coverage_bot.models.events import EventStatus, TrackedEvent
from live_coverage_bot.models.markets import MarketComparison

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"


class SlackError(Exception):
    """Error raised when Slack API operations fail."""


class SlackClient:
    """Async Slack Web API client for threaded event lifecycle alerts."""

    def __init__(self, config: SlackConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=SLACK_API_BASE,
            timeout=10.0,
            headers={"Authorization": f"Bearer {config.bot_token}"},
        )

    def format_parent_message(
        self, event: TrackedEvent, now: datetime | None = None
    ) -> str:
        """Format the parent Slack message based on current event status."""
        provider_str = ", ".join(
            f"{p.type} #{p.id}" for p in event.provider_ids
        )
        competition_line = event.competition
        if event.country:
            competition_line += f" | {event.country}"

        kickoff_str = event.scheduled_kickoff.strftime("%H:%M UTC")

        if event.status == EventStatus.LATE:
            if now is None:
                now = datetime.now(tz=UTC)
            delay_min = int((now - event.scheduled_kickoff).total_seconds() / 60)
            return (
                f"\U0001f7e1 LATE \u2014 {event.home_team} vs {event.away_team}\n"
                f"\U0001f4cb {competition_line}\n"
                f"\u23f0 Kickoff: {kickoff_str} | Now: {delay_min}min late\n"
                f"\U0001f50c {provider_str}\n"
                f"\U0001f194 BetPawa ID: {event.betpawa_event_id}"
            )

        if event.status == EventStatus.LIVE:
            live_str = ""
            delay_str = ""
            if event.first_seen_live:
                live_str = event.first_seen_live.strftime("%H:%M UTC")
            if event.transition_delay_sec is not None:
                delay_min = event.transition_delay_sec // 60
                delay_str = f"+{delay_min}min"
            return (
                f"\U0001f7e2 WENT LIVE \u2014 {event.home_team} vs {event.away_team}\n"
                f"\U0001f4cb {competition_line}\n"
                f"\u23f0 Kickoff: {kickoff_str} | Live at: {live_str} ({delay_str})\n"
                f"\U0001f50c {provider_str}\n"
                f"\U0001f194 BetPawa ID: {event.betpawa_event_id}"
            )

        if event.status == EventStatus.NEVER_LIVE:
            return (
                f"\U0001f534 NEVER LIVE \u2014 {event.home_team} vs {event.away_team}\n"
                f"\U0001f4cb {competition_line}\n"
                f"\u23f0 Kickoff: {kickoff_str} | Timed out after "
                f"{int((datetime.now(tz=UTC) - event.scheduled_kickoff).total_seconds() / 60)}min\n"
                f"\U0001f50c {provider_str}\n"
                f"\U0001f194 BetPawa ID: {event.betpawa_event_id}"
            )

        return f"{event.home_team} vs {event.away_team} [{event.status}] | BetPawa ID: {event.betpawa_event_id}"

    def format_thread_reply(
        self,
        old_status: EventStatus,
        new_status: EventStatus,
        changed_at: datetime,
        details: str | None = None,
    ) -> str:
        """Format a thread reply for a state change."""
        time_str = changed_at.strftime("%H:%M")
        detail_str = f" \u2014 {details}" if details else ""

        if new_status == EventStatus.LATE:
            return f"{time_str} \u2014 \u26a0\ufe0f Not live yet{detail_str}"
        if new_status == EventStatus.LIVE:
            return f"{time_str} \u2014 \u2705 Went live{detail_str}"
        if new_status == EventStatus.NEVER_LIVE:
            return f"{time_str} \u2014 \U0001f6d1 Never went live{detail_str}"

        return f"{time_str} \u2014 {old_status} \u2192 {new_status}{detail_str}"

    async def post_alert(self, event: TrackedEvent, now: datetime) -> str:
        """Post a new alert message. Returns the message ts."""
        text = self.format_parent_message(event, now)
        response = await self._client.post(
            "/chat.postMessage",
            json={"channel": self._config.channel_id, "text": text},
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackError(f"Slack API error: {data.get('error', 'unknown')}")
        return data["ts"]

    async def update_message(
        self, ts: str, event: TrackedEvent, now: datetime | None = None
    ) -> None:
        """Update an existing parent message."""
        text = self.format_parent_message(event, now)
        response = await self._client.post(
            "/chat.update",
            json={"channel": self._config.channel_id, "ts": ts, "text": text},
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackError(f"Slack API error: {data.get('error', 'unknown')}")

    async def post_thread_reply(self, thread_ts: str, text: str) -> None:
        """Post a reply in a message thread."""
        response = await self._client.post(
            "/chat.postMessage",
            json={
                "channel": self._config.channel_id,
                "thread_ts": thread_ts,
                "text": text,
            },
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackError(f"Slack API error: {data.get('error', 'unknown')}")

    async def post_summary(self, text: str) -> None:
        """Post a weekly summary message to the summary channel."""
        channel = self._config.summary_channel_id or self._config.channel_id
        response = await self._client.post(
            "/chat.postMessage",
            json={"channel": channel, "text": text},
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackError(f"Slack API error: {data.get('error', 'unknown')}")

    def format_market_recap(
        self,
        comparison: "MarketComparison",
        now: datetime,
    ) -> str:
        """Format a market comparison as a thread reply."""
        time_str = now.strftime("%H:%M")
        total_prematch = comparison.markets_dropped + comparison.markets_kept

        lines: list[str] = [
            f"{time_str} \u2014 \U0001f4ca Market comparison (prematch {comparison.prematch_phase} \u2192 live)",
            f" \u2022 {total_prematch} prematch markets \u2192 {comparison.markets_kept} live "
            f"({comparison.retention_pct:.0f}% retention)",
        ]

        dropped_names = comparison.details.get("dropped", [])
        if dropped_names:
            lines.append(f" \u2022 Dropped: {comparison.markets_dropped} markets")
            for name in dropped_names[:5]:
                lines.append(f"   - {name}")
            if len(dropped_names) > 5:
                lines.append(f"   - ... and {len(dropped_names) - 5} more")

        if comparison.dropped_key_markets:
            lines.append(
                f" \u2022 \u26a0\ufe0f Key market(s) dropped: "
                f"{', '.join(comparison.dropped_key_markets)}"
            )

        odds_shifts = comparison.details.get("odds_shifts", [])
        if odds_shifts:
            biggest = max(odds_shifts, key=lambda s: s.get("shift_pct", 0))
            lines.append(
                f" \u2022 Biggest odds shift: {biggest['market']} "
                f"{biggest['selection']} "
                f"{biggest['prematch_price']} \u2192 {biggest['live_price']} "
                f"({biggest['shift_pct']:+.1f}%)"
            )

        return "\n".join(lines)

    def format_market_anomaly_parent(
        self,
        event: TrackedEvent,
        comparison: "MarketComparison",
    ) -> str:
        """Format a standalone 'market anomaly' parent message for happy-path events."""
        provider_str = ", ".join(f"{p.type} #{p.id}" for p in event.provider_ids)
        competition_line = event.competition
        if event.country:
            competition_line += f" | {event.country}"
        kickoff_str = event.scheduled_kickoff.strftime("%H:%M UTC")
        total_prematch = comparison.markets_dropped + comparison.markets_kept

        lines = [
            f"\U0001f4ca MARKET ANOMALY \u2014 {event.home_team} vs {event.away_team}",
            f"\U0001f4cb {competition_line}",
            f"\u23f0 Kickoff: {kickoff_str} | Went live on time",
            f"\U0001f50c {provider_str}",
            f"\U0001f194 BetPawa ID: {event.betpawa_event_id}",
            "",
            f"{total_prematch} prematch markets \u2192 {comparison.markets_kept} live "
            f"({comparison.retention_pct:.0f}% retention)",
        ]
        if comparison.dropped_key_markets:
            lines.append(
                f"\u26a0\ufe0f Key market dropped: "
                f"{', '.join(comparison.dropped_key_markets)}"
            )
        if comparison.max_odds_shift_pct > 0:
            lines.append(f"Biggest odds shift: {comparison.max_odds_shift_pct:+.1f}%")
        return "\n".join(lines)

    async def post_market_recap(self, thread_ts: str, text: str) -> None:
        """Post a market comparison recap as a reply in an existing event thread."""
        response = await self._client.post(
            "/chat.postMessage",
            json={
                "channel": self._config.channel_id,
                "thread_ts": thread_ts,
                "text": text,
            },
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackError(f"Slack API error: {data.get('error', 'unknown')}")

    async def post_market_anomaly_alert(
        self, event: TrackedEvent, text: str
    ) -> str:
        """Post a standalone parent message for a market anomaly. Returns ts."""
        response = await self._client.post(
            "/chat.postMessage",
            json={"channel": self._config.channel_id, "text": text},
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackError(f"Slack API error: {data.get('error', 'unknown')}")
        return data["ts"]

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
