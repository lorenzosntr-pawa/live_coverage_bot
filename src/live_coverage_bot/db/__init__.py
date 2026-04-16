"""Database layer for event persistence."""

from live_coverage_bot.db.connection import Database
from live_coverage_bot.db.repository import EventRepository

__all__ = [
    "Database",
    "EventRepository",
]
