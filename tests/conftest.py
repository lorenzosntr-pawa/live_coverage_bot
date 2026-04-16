"""Shared test fixtures."""

import pytest

from live_coverage_bot.db.connection import Database


@pytest.fixture
async def db():
    """Create an in-memory database for testing."""
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()
