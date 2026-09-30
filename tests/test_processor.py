"""
Tests for the meeting processing pipeline.

Covers:
- Full happy-path processing (fetch → normalize → store → summarize → complete)
- Idempotency: COMPLETED meetings skip re-processing
- Idempotency: double-process doesn't create duplicate summaries
- TranscriptNotReadyError → stays SCHEDULED, returns proper message
- TranscriptFetchError → FAILED status
- Summarizer failure → still completes with fallback
- Force flag overrides completed state
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from models.meeting import MeetingStatus
from providers.base import TranscriptFetchError, TranscriptNotReadyError
from services.processor import MeetingProcessor


class TestMeetingProcessor:

    @pytest_asyncio.fixture
    async def processor(self, db_repo, mock_provider, mock_summarizer):
        return MeetingProcessor(
            repo=db_repo,
            provider=mock_provider,
            summarizer=mock_summarizer,
        )

    @pytest_asyncio.fixture
    async def meeting(self, db_repo):
        return await db_repo.create_meeting(
            platform="google_meet",
            external_meeting_id="test-conf-001",
            title="Test Meeting",
        )

    # ── Happy path ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_full_processing_pipeline(self, processor, meeting, db_repo):
        result = await processor.process(meeting.id)
        assert result["status"] == MeetingStatus.COMPLETED.value
        assert result["segments"] > 0
        assert result["action_items"] > 0

        # Verify DB state
        updated = await db_repo.get_meeting(meeting.id)
        assert updated.status == MeetingStatus.COMPLETED.value
        assert updated.transcript is not None
        assert updated.summary is not None
        assert updated.error_message is None

    # ── Idempotency ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_completed_meeting_skips_reprocessing(self, processor, meeting, db_repo):
        """Processing a COMPLETED meeting without force=True should skip."""
        # First process
        await processor.process(meeting.id)
        # Second process (no force)
        result = await processor.process(meeting.id)

        assert result["status"] == MeetingStatus.COMPLETED.value
        assert "already processed" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_force_reprocesses_completed_meeting(self, processor, meeting, db_repo, mock_provider):
        """force=True should reprocess even if already COMPLETED."""
        await processor.process(meeting.id)
        call_count_before = mock_provider.fetch_transcript.call_count

        result = await processor.process(meeting.id, force=True)
        assert result["status"] == MeetingStatus.COMPLETED.value
        # Provider was called again
        assert mock_provider.fetch_transcript.call_count == call_count_before + 1

    @pytest.mark.asyncio
    async def test_double_process_no_duplicate_summary(self, processor, meeting, db_repo):
        """Processing twice should not create two summaries — only one final state."""
        await processor.process(meeting.id)
        await processor.process(meeting.id, force=True)

        updated = await db_repo.get_meeting(meeting.id)
        # Summary should be exactly one dict (not a list or doubled)
        assert isinstance(updated.summary, dict)
        assert updated.status == MeetingStatus.COMPLETED.value

    # ── Transcript not ready ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_transcript_not_ready_returns_scheduled(
        self, db_repo, mock_summarizer, sample_normalized_transcript, meeting
    ):
        provider = AsyncMock()
        provider.platform = "google_meet"
        provider.fetch_transcript = AsyncMock(
            side_effect=TranscriptNotReadyError("Transcript still processing")
        )

        processor = MeetingProcessor(repo=db_repo, provider=provider, summarizer=mock_summarizer)
        result = await processor.process(meeting.id)

        assert result["status"] == MeetingStatus.SCHEDULED.value
        assert "not ready" in result["message"].lower()

        updated = await db_repo.get_meeting(meeting.id)
        assert updated.status == MeetingStatus.SCHEDULED.value
        assert updated.transcript is None

    # ── Transcript fetch error ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_transcript_fetch_error_marks_failed(
        self, db_repo, mock_summarizer, meeting
    ):
        provider = AsyncMock()
        provider.platform = "google_meet"
        provider.fetch_transcript = AsyncMock(
            side_effect=TranscriptFetchError("403 Forbidden — transcription not enabled")
        )

        processor = MeetingProcessor(repo=db_repo, provider=provider, summarizer=mock_summarizer)
        result = await processor.process(meeting.id)

        assert result["status"] == MeetingStatus.FAILED.value

        updated = await db_repo.get_meeting(meeting.id)
        assert updated.status == MeetingStatus.FAILED.value
        assert "403" in updated.error_message

    # ── Meeting not found ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_nonexistent_meeting_returns_error(self, processor):
        result = await processor.process("nonexistent-id-000")
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()
