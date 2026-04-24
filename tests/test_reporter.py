"""Tests for weekly report generator."""

import csv
import io
from datetime import UTC, datetime

import pytest

from live_coverage_bot.clients.models import ProviderID, ProviderType
from live_coverage_bot.core.reporter import WeeklyReporter
from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository
from live_coverage_bot.models.events import EventStatus, TrackedEvent


@pytest.fixture
def repo(db: Database) -> EventRepository:
    return EventRepository(db)


@pytest.fixture
def reporter(repo: EventRepository) -> WeeklyReporter:
    return WeeklyReporter(repo, week_starts="tuesday")


async def _insert_events(repo: EventRepository):
    """Insert a mix of events for testing."""
    base = datetime(2026, 4, 14, tzinfo=UTC)
    pids_sr = [ProviderID(type=ProviderType.SPORTRADAR, id="100")]
    pids_gs = [ProviderID(type=ProviderType.GENIUSSPORTS, id="200")]

    events = [
        TrackedEvent(
            betpawa_event_id="1", home_team="A", away_team="B",
            competition="EPL", country="England",
            scheduled_kickoff=base, status=EventStatus.LIVE,
            provider_ids=pids_sr, first_seen_prematch=base,
            first_seen_live=base, transition_delay_sec=30,
        ),
        TrackedEvent(
            betpawa_event_id="2", home_team="C", away_team="D",
            competition="EPL", country="England",
            scheduled_kickoff=base, status=EventStatus.LIVE,
            provider_ids=pids_sr, first_seen_prematch=base,
            first_seen_live=base, transition_delay_sec=60,
        ),
        TrackedEvent(
            betpawa_event_id="3", home_team="E", away_team="F",
            competition="LaLiga", country="Spain",
            scheduled_kickoff=base, status=EventStatus.LIVE,
            provider_ids=pids_gs, first_seen_prematch=base,
            first_seen_live=base, transition_delay_sec=600,
        ),
        TrackedEvent(
            betpawa_event_id="4", home_team="G", away_team="H",
            competition="NPFL", country="Nigeria",
            scheduled_kickoff=base, status=EventStatus.NEVER_LIVE,
            provider_ids=pids_gs, first_seen_prematch=base,
        ),
        TrackedEvent(
            betpawa_event_id="5", home_team="I", away_team="J",
            competition="EPL", country="England",
            scheduled_kickoff=base, status=EventStatus.REMOVED,
            provider_ids=pids_sr, first_seen_prematch=base,
        ),
        TrackedEvent(
            betpawa_event_id="6", home_team="K", away_team="L",
            competition="EPL", country="England",
            scheduled_kickoff=base, status=EventStatus.UNMONITORED,
            provider_ids=pids_sr, first_seen_prematch=base,
        ),
        TrackedEvent(
            betpawa_event_id="7", home_team="M", away_team="N",
            competition="NPFL", country="Nigeria",
            scheduled_kickoff=base, status=EventStatus.UNMONITORED,
            provider_ids=pids_gs, first_seen_prematch=base,
        ),
    ]
    for e in events:
        await repo.insert_event(e)


class TestWeeklyReporter:
    async def test_generate_slack_summary(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        assert "Total prematch events tracked: 7" in summary
        assert "SPORTRADAR" in summary
        assert "GENIUSSPORTS" in summary

    async def test_generate_csv_content(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        csv_content = await reporter.generate_csv(start, end)
        reader = csv.DictReader(io.StringIO(csv_content))
        rows = list(reader)
        assert len(rows) == 7
        assert "betpawa_event_id" in rows[0]
        assert "status" in rows[0]

    async def test_compute_report_period_tuesday_week(self, reporter):
        report_time = datetime(2026, 4, 14, 8, 0, tzinfo=UTC)
        start, end = reporter.compute_report_period(report_time)
        assert start.day == 7
        assert end.day == 13

    async def test_empty_report(self, reporter, repo):
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        assert "Total prematch events tracked: 0" in summary


class TestUnmonitoredInReport:
    async def test_summary_includes_unmonitored_line(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        assert "Unmonitored" in summary or "unmonitored" in summary
        assert "2" in summary  # 2 unmonitored events

    async def test_summary_total_includes_unmonitored(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        assert "Total prematch events tracked: 7" in summary

    async def test_unmonitored_excluded_from_problem_events(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        lines = summary.split("\n")
        npfl_lines = [l for l in lines if "NPFL" in l]
        for line in npfl_lines:
            assert "unmonitored" not in line.lower()

    async def test_csv_includes_unmonitored_status(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        csv_content = await reporter.generate_csv(start, end)
        assert "UNMONITORED" in csv_content

    async def test_unmonitored_excluded_from_provider_stats(self, reporter, repo):
        await _insert_events(repo)
        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        assert "SPORTRADAR" in summary
        lines = summary.split("\n")
        sr_line = next(l for l in lines if "SPORTRADAR" in l)
        assert "3 events" in sr_line


class TestLateReasonReport:
    async def test_summary_includes_late_reason_breakdown(self, reporter, repo):
        base = datetime(2026, 4, 14, tzinfo=UTC)
        pids = [ProviderID(type=ProviderType.SPORTRADAR, id="100")]
        events = [
            TrackedEvent(
                betpawa_event_id="10", home_team="A", away_team="B",
                competition="EPL", country="England",
                scheduled_kickoff=base, status=EventStatus.LIVE,
                provider_ids=pids, first_seen_prematch=base,
                first_seen_live=base, transition_delay_sec=600,
                late_reason="COVERAGE_LATE", live_minute=12,
            ),
            TrackedEvent(
                betpawa_event_id="11", home_team="C", away_team="D",
                competition="EPL", country="England",
                scheduled_kickoff=base, status=EventStatus.LIVE,
                provider_ids=pids, first_seen_prematch=base,
                first_seen_live=base, transition_delay_sec=720,
                late_reason="MATCH_DELAYED", live_minute=3,
            ),
            TrackedEvent(
                betpawa_event_id="12", home_team="E", away_team="F",
                competition="EPL", country="England",
                scheduled_kickoff=base, status=EventStatus.LIVE,
                provider_ids=pids, first_seen_prematch=base,
                first_seen_live=base, transition_delay_sec=0,
                late_reason="KICKOFF_RESCHEDULED",
            ),
        ]
        for e in events:
            await repo.insert_event(e)

        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        summary = await reporter.generate_slack_summary(start, end)
        assert "Coverage late" in summary or "coverage late" in summary
        assert "Match delayed" in summary or "match delayed" in summary
        assert "Rescheduled" in summary or "rescheduled" in summary

    async def test_csv_includes_late_reason_columns(self, reporter, repo):
        base = datetime(2026, 4, 14, tzinfo=UTC)
        pids = [ProviderID(type=ProviderType.SPORTRADAR, id="100")]
        event = TrackedEvent(
            betpawa_event_id="20", home_team="G", away_team="H",
            competition="EPL", country="England",
            scheduled_kickoff=base, status=EventStatus.LIVE,
            provider_ids=pids, first_seen_prematch=base,
            first_seen_live=base, transition_delay_sec=600,
            late_reason="COVERAGE_LATE", live_minute=15,
        )
        await repo.insert_event(event)

        start = datetime(2026, 4, 13, tzinfo=UTC)
        end = datetime(2026, 4, 15, tzinfo=UTC)
        csv_content = await reporter.generate_csv(start, end)
        reader = csv.DictReader(io.StringIO(csv_content))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["late_reason"] == "COVERAGE_LATE"
        assert rows[0]["live_minute"] == "15"
