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

# Slack message emoji constants
EMOJI_LATE = "\U0001f7e1"        # 🟡
EMOJI_LIVE = "\U0001f7e2"        # 🟢
EMOJI_NEVER_LIVE = "\U0001f534"  # 🔴
EMOJI_CLIPBOARD = "\U0001f4cb"   # 📋
EMOJI_CLOCK = "\u23f0"           # ⏰
EMOJI_PLUG = "\U0001f50c"        # 🔌
EMOJI_ID = "\U0001f194"          # 🆔
EMOJI_WARNING = "\u26a0\ufe0f"   # ⚠️
EMOJI_CHECK = "\u2705"           # ✅
EMOJI_STOP = "\U0001f6d1"        # 🛑
EMOJI_CHART = "\U0001f4ca"       # 📊
EMOJI_DASH = "\u2014"            # —


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

    def _format_event_header(
        self, event: TrackedEvent
    ) -> tuple[str, str, str]:
        """Extract common header fields: (provider_str, competition_line, kickoff_str)."""
        provider_str = ", ".join(
            f"{p.type} #{p.id}" for p in event.provider_ids
        )
        competition_line = event.competition
        if event.country:
            competition_line += f" | {event.country}"
        kickoff_str = event.scheduled_kickoff.strftime("%H:%M UTC")
        return provider_str, competition_line, kickoff_str

    def format_parent_message(
        self, event: TrackedEvent, now: datetime | None = None
    ) -> str:
        """Format the parent Slack message based on current event status."""
        provider_str, competition_line, kickoff_str = self._format_event_header(event)

        if event.status == EventStatus.LATE:
            if now is None:
                now = datetime.now(tz=UTC)
            delay_min = int((now - event.scheduled_kickoff).total_seconds() / 60)
            return (
                f"{EMOJI_LATE} LATE {EMOJI_DASH} {event.home_team} vs {event.away_team}\n"
                f"{EMOJI_CLIPBOARD} {competition_line}\n"
                f"{EMOJI_CLOCK} Kickoff: {kickoff_str} | Now: {delay_min}min late\n"
                f"{EMOJI_PLUG} {provider_str}\n"
                f"{EMOJI_ID} BetPawa ID: {event.betpawa_event_id}"
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
                f"{EMOJI_LIVE} WENT LIVE {EMOJI_DASH} {event.home_team} vs {event.away_team}\n"
                f"{EMOJI_CLIPBOARD} {competition_line}\n"
                f"{EMOJI_CLOCK} Kickoff: {kickoff_str} | Live at: {live_str} ({delay_str})\n"
                f"{EMOJI_PLUG} {provider_str}\n"
                f"{EMOJI_ID} BetPawa ID: {event.betpawa_event_id}"
            )

        if event.status == EventStatus.NEVER_LIVE:
            return (
                f"{EMOJI_NEVER_LIVE} NEVER LIVE {EMOJI_DASH} {event.home_team} vs {event.away_team}\n"
                f"{EMOJI_CLIPBOARD} {competition_line}\n"
                f"{EMOJI_CLOCK} Kickoff: {kickoff_str} | Timed out after "
                f"{int((datetime.now(tz=UTC) - event.scheduled_kickoff).total_seconds() / 60)}min\n"
                f"{EMOJI_PLUG} {provider_str}\n"
                f"{EMOJI_ID} BetPawa ID: {event.betpawa_event_id}"
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
        detail_str = f" {EMOJI_DASH} {details}" if details else ""

        if new_status == EventStatus.LATE:
            return f"{time_str} {EMOJI_DASH} {EMOJI_WARNING} Not live yet{detail_str}"
        if new_status == EventStatus.LIVE:
            return f"{time_str} {EMOJI_DASH} {EMOJI_CHECK} Went live{detail_str}"
        if new_status == EventStatus.NEVER_LIVE:
            return f"{time_str} {EMOJI_DASH} {EMOJI_STOP} Never went live{detail_str}"

        return f"{time_str} {EMOJI_DASH} {old_status} \u2192 {new_status}{detail_str}"

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
            f"{time_str} {EMOJI_DASH} {EMOJI_CHART} Market comparison (prematch {comparison.prematch_phase} \u2192 live)",
            f" \u2022 {total_prematch} prematch markets \u2192 {comparison.markets_kept} live "
            f"({comparison.retention_pct:.0f}% retention)",
        ]

        dropped_names = [d.market_type_name for d in comparison.details.dropped]
        if dropped_names:
            lines.append(f" \u2022 Dropped: {comparison.markets_dropped} markets")
            for name in dropped_names[:5]:
                lines.append(f"   - {name}")
            if len(dropped_names) > 5:
                lines.append(f"   - ... and {len(dropped_names) - 5} more")

        if comparison.dropped_key_markets:
            lines.append(
                f" \u2022 {EMOJI_WARNING} Key market(s) dropped: "
                f"{', '.join(comparison.dropped_key_markets)}"
            )

        odds_shifts = comparison.details.odds_shifts
        if odds_shifts:
            biggest = max(odds_shifts, key=lambda s: s.shift_pct)
            lines.append(
                f" \u2022 Biggest odds shift: {biggest.market_type_name} "
                f"{biggest.selection_name} "
                f"{biggest.prematch_price} \u2192 {biggest.live_price} "
                f"({biggest.shift_pct:+.1f}%)"
            )

        return "\n".join(lines)

    def format_missing_markets(
        self,
        dropped: list,
        key_market_names: list[str],
    ) -> str:
        """Format the missing markets section for a thread reply."""
        if not dropped:
            return ""

        key_set = set(key_market_names)
        key_dropped = [d for d in dropped if d.market_type_name in key_set]
        non_key_dropped = [d for d in dropped if d.market_type_name not in key_set]
        max_non_key = 7

        lines = [f"\u274c Missing markets ({len(dropped)} dropped):"]
        for d in key_dropped:
            lines.append(f"  \u2022 {d.market_type_name} {EMOJI_WARNING} KEY")
        for d in non_key_dropped[:max_non_key]:
            lines.append(f"  \u2022 {d.market_type_name}")
        remaining = len(non_key_dropped) - max_non_key
        if remaining > 0:
            lines.append(f"  ... and {remaining} more")

        return "\n".join(lines)

    def format_odds_shifts(
        self,
        shifts: list,
        threshold: float = 5.0,
    ) -> str:
        """Format per-selection odds shifts grouped by market."""
        from collections import defaultdict

        significant = [s for s in shifts if abs(s.shift_pct) >= threshold]
        if not significant:
            return f"{EMOJI_CHART} Odds shifts (prematch \u2192 live):\n  No significant odds shifts."

        by_market: dict[str, list] = defaultdict(list)
        for s in significant:
            by_market[s.market_type_name].append(s)

        lines = [f"{EMOJI_CHART} Odds shifts (prematch \u2192 live):"]
        for market_name, sels in by_market.items():
            lines.append(f"  {market_name}:")
            for s in sels:
                sign = "+" if s.live_price >= s.prematch_price else ""
                pct = s.shift_pct if s.live_price >= s.prematch_price else -s.shift_pct
                lines.append(
                    f"    {s.selection_name}  {s.prematch_price:.2f} \u2192 "
                    f"{s.live_price:.2f} ({sign}{pct:.1f}%)"
                )

        return "\n".join(lines)

    def format_snapshot_update(
        self,
        match_state,
        phase_label: str,
        prev_kept: int,
        curr_kept: int,
        prev_retention: float,
        curr_retention: float,
        recovered: list[str],
        still_missing: list[str],
        key_markets: list[str],
    ) -> str:
        """Format a follow-up snapshot thread update (LIVE_2, LIVE_5)."""
        lines: list[str] = []
        if match_state:
            lines.append(match_state.display)

        lines.append(
            f"{EMOJI_CHART} Snapshot {phase_label}: "
            f"{prev_kept} \u2192 {curr_kept} markets "
            f"({prev_retention:.0f}% \u2192 {curr_retention:.0f}% retention)"
        )

        key_set = set(key_markets)
        if recovered:
            lines.append(f"{EMOJI_CHECK} Recovered: {', '.join(recovered)}")
        if still_missing:
            tagged = []
            for name in still_missing:
                tag = f" {EMOJI_WARNING} KEY" if name in key_set else ""
                tagged.append(f"{name}{tag}")
            lines.append(f"\u274c Still missing: {', '.join(tagged)}")

        return "\n".join(lines)

    def format_market_anomaly_parent(
        self,
        event: TrackedEvent,
        comparison: "MarketComparison",
    ) -> str:
        """Format a standalone 'market anomaly' parent message."""
        provider_str, competition_line, kickoff_str = self._format_event_header(event)
        total_prematch = comparison.markets_dropped + comparison.markets_kept

        significant_shifts = sum(
            1 for s in comparison.details.odds_shifts if abs(s.shift_pct) >= 5.0
        )

        lines = [
            f"{EMOJI_CHART} MARKET ANOMALY {EMOJI_DASH} {event.home_team} vs {event.away_team}",
            f"{EMOJI_CLIPBOARD} {competition_line}",
            f"{EMOJI_CLOCK} Kickoff: {kickoff_str} | Went live on time",
            f"{EMOJI_PLUG} {provider_str}",
            f"{EMOJI_ID} BetPawa ID: {event.betpawa_event_id}",
            "",
            f"{total_prematch} prematch markets \u2192 {comparison.markets_kept} live "
            f"({comparison.retention_pct:.0f}% retention)",
            f"\u274c {comparison.markets_dropped} markets dropped, "
            f"{significant_shifts} selections shifted > 5%",
        ]
        if comparison.dropped_key_markets:
            lines.append(
                f"{EMOJI_WARNING} Key market dropped: "
                f"{', '.join(comparison.dropped_key_markets)}"
            )
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
