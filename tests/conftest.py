"""
Pytest configuration and shared fixtures for the fireflies-notetaker test suite.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# Add backend to path so test modules can import from it directly
BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))


# ── Event loop ────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── Sample transcript fixture ─────────────────────────────────────────────

@pytest.fixture(scope="session")
def sample_transcript_dict() -> dict:
    """Load the sample transcript JSON fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / "sample_google_meet_transcript.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def sample_normalized_transcript(sample_transcript_dict):
    """Return a NormalizedTranscript built from the fixture data."""
    from models.transcript import NormalizedTranscript
    return NormalizedTranscript.from_dict(sample_transcript_dict)


# ── In-memory database ────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_repo():
    """Create a fresh in-memory SQLite repo for each test."""
    from database.repository import init_db
    # Use an in-memory SQLite URL (aiosqlite supports it)
    repo = await init_db("sqlite+aiosqlite:///:memory:")
    return repo


# ── Mock provider ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_provider(sample_normalized_transcript):
    """A mock provider that returns the sample transcript."""
    from providers.base import AbstractMeetingProvider
    provider = AsyncMock(spec=AbstractMeetingProvider)
    provider.platform = "google_meet"
    provider.fetch_transcript = AsyncMock(return_value=sample_normalized_transcript)
    return provider


# ── Mock summarizer ───────────────────────────────────────────────────────

@pytest.fixture
def mock_summarizer():
    """A mock summarizer returning a preset summary."""
    from ai.summarizer import MeetingSummarizer
    from models.meeting import ActionItem, MeetingSummary
    summarizer = AsyncMock(spec=MeetingSummarizer)
    summarizer.summarize = AsyncMock(return_value=MeetingSummary(
        overview="Team discussed Q4 roadmap priorities and assigned action items.",
        key_points=[
            "OAuth login redirect latency to be fixed by moving DB lookup to background",
            "Google Meet provider implementation to be completed this sprint",
        ],
        decisions=[
            "Use a single LLM call for full transcript, add chunking later",
            "Add frontend polling for SUMMARIZING status",
        ],
        action_items=[
            ActionItem(task="Fix OAuth latency with background DB lookup PR", owner="Arjun Mehta", due_date="Wednesday"),
            ActionItem(task="Implement Google Meet provider mock", owner="Priya Sharma", due_date="Thursday"),
            ActionItem(task="Add frontend polling for SUMMARIZING status", owner="Govind J", due_date=None),
            ActionItem(task="Open GitHub issue for transcript chunking", owner="Arjun Mehta", due_date=None),
        ],
        participants=["Govind J", "Priya Sharma", "Arjun Mehta"],
    ))
    summarizer.extract_title = AsyncMock(return_value="Q4 Roadmap Planning")
    return summarizer


# ── FastAPI test client ───────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_app(db_repo, mock_summarizer):
    """FastAPI test app with in-memory DB and mock summarizer."""
    from fastapi.testclient import TestClient

    # Patch singletons before importing routes
    import database.repository as db_mod
    import ai.summarizer as ai_mod

    original_repo = db_mod._repo
    original_summ = ai_mod._summarizer_instance

    db_mod._repo = db_repo
    ai_mod._summarizer_instance = mock_summarizer

    from main import app
    client = TestClient(app)

    yield client

    db_mod._repo = original_repo
    ai_mod._summarizer_instance = original_summ
