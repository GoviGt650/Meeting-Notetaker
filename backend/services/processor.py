"""
Meeting processing pipeline (orchestrator).

This is the core of the system. It coordinates:
  1. Status transitions through the state machine
  2. Transcript fetching via the provider
  3. Transcript normalization and storage
  4. AI summarization
  5. Summary storage
  6. Final COMPLETED status

The processor is IDEMPOTENT:
  - If called on a COMPLETED meeting, it returns early (no duplicate summary).
  - If called on a FAILED meeting, it resets and retries.
  - If called while already SUMMARIZING/PROCESSING, it returns early.

Usage:
    processor = MeetingProcessor(repo, provider, summarizer)
    await processor.process(meeting_id)

For manual trigger via API:
    POST /meetings/{id}/process
"""

from __future__ import annotations

import logging
from typing import Optional

from ai.summarizer import MeetingSummarizer
from database.repository import MeetingRepository
from models.meeting import MeetingStatus
from models.transcript import NormalizedTranscript
from providers.base import AbstractMeetingProvider, TranscriptFetchError, TranscriptNotReadyError

logger = logging.getLogger(__name__)

# States that indicate processing is already underway — skip re-entry
_IN_PROGRESS_STATES = {
    MeetingStatus.CAPTURING.value,
    MeetingStatus.PROCESSING.value,
    MeetingStatus.SUMMARIZING.value,
}

# States that are terminal and should not be re-processed unless explicitly requested
_TERMINAL_STATES = {MeetingStatus.COMPLETED.value}


class MeetingProcessor:
    """Orchestrates the full transcript → summary pipeline for a single meeting.

    This class has NO knowledge of Google Meet, Zoom, Teams, or any platform.
    It depends only on the AbstractMeetingProvider interface.
    """

    def __init__(
        self,
        repo: MeetingRepository,
        provider: AbstractMeetingProvider,
        summarizer: MeetingSummarizer,
    ) -> None:
        self._repo = repo
        self._provider = provider
        self._summarizer = summarizer

    async def process(
        self,
        meeting_id: str,
        force: bool = False,
    ) -> dict:
        """Run the full processing pipeline for a meeting.

        Args:
            meeting_id:  Internal UUID of the meeting to process.
            force:       If True, reprocess even if already COMPLETED.

        Returns:
            dict with keys: meeting_id, status, message
        """
        # ── 1. Load meeting ───────────────────────────────────────────────
        meeting = await self._repo.get_meeting(meeting_id)
        if not meeting:
            return {"meeting_id": meeting_id, "status": "error", "message": "Meeting not found"}

        current_status = meeting.status

        # ── 2. Idempotency check ──────────────────────────────────────────
        if not force and current_status in _TERMINAL_STATES:
            logger.info(
                "[processor] Meeting %s already %s — skipping", meeting_id, current_status
            )
            return {
                "meeting_id": meeting_id,
                "status": current_status,
                "message": "Meeting already processed",
            }

        if not force and current_status in _IN_PROGRESS_STATES:
            logger.info(
                "[processor] Meeting %s is %s — already running", meeting_id, current_status
            )
            return {
                "meeting_id": meeting_id,
                "status": current_status,
                "message": "Processing already in progress",
            }

        # ── 3. Fetch transcript ───────────────────────────────────────────
        logger.info("[processor] Starting processing for meeting %s", meeting_id)
        await self._repo.update_status(meeting_id, MeetingStatus.CAPTURING)

        external_id = meeting.external_meeting_id or meeting_id
        try:
            transcript = await self._provider.fetch_transcript(
                meeting_id=meeting_id,
                external_meeting_id=external_id,
                start_time=meeting.start_time,
            )
        except TranscriptNotReadyError as e:
            logger.warning(
                "[processor] Transcript not ready for meeting %s: %s", meeting_id, e
            )
            await self._repo.update_status(
                meeting_id,
                MeetingStatus.SCHEDULED,
                error_message=f"Transcript not ready: {e}",
            )
            return {
                "meeting_id": meeting_id,
                "status": MeetingStatus.SCHEDULED.value,
                "message": f"Transcript not ready yet: {e}",
            }
        except TranscriptFetchError as e:
            logger.error(
                "[processor] Transcript fetch failed for meeting %s: %s", meeting_id, e
            )
            await self._repo.fail_meeting(meeting_id, str(e))
            return {
                "meeting_id": meeting_id,
                "status": MeetingStatus.FAILED.value,
                "message": f"Transcript fetch failed: {e}",
            }
        except Exception as e:
            logger.error(
                "[processor] Unexpected error fetching transcript for meeting %s: %s",
                meeting_id, e,
            )
            await self._repo.fail_meeting(meeting_id, f"Unexpected error: {e}")
            return {
                "meeting_id": meeting_id,
                "status": MeetingStatus.FAILED.value,
                "message": f"Unexpected error: {e}",
            }

        # ── 4. Store transcript ───────────────────────────────────────────
        await self._repo.update_status(meeting_id, MeetingStatus.PROCESSING)
        await self._repo.store_transcript(meeting_id, transcript)

        # ── 5. Summarize ──────────────────────────────────────────────────
        logger.info("[processor] Starting summarization for meeting %s", meeting_id)
        await self._repo.update_status(meeting_id, MeetingStatus.SUMMARIZING)

        try:
            summary = await self._summarizer.summarize(transcript)
        except Exception as e:
            # summarize() already returns a fallback on failure; this catches
            # any truly unexpected exception
            logger.error(
                "[processor] Summarization crashed for meeting %s: %s", meeting_id, e
            )
            from models.meeting import MeetingSummary
            summary = MeetingSummary(
                overview=f"Summarization failed: {e}",
                key_points=[],
                decisions=[],
                action_items=[],
                participants=[],
            )

        # ── 6. Determine title ────────────────────────────────────────────
        title = meeting.title
        if not title:
            # Use the LLM-generated title from the summary, or extract one
            title = None  # Will be set from summary if summary has no title field
            # summary is a MeetingSummary pydantic model — it doesn't have a 'title'
            # field directly. We'll derive the title from the meeting data.
            # If we need a title, call the summarizer's extract_title method.
            try:
                title = await self._summarizer.extract_title(transcript)
            except Exception:
                title = f"Meeting {meeting_id[:8]}"

        # ── 7. Store summary + mark COMPLETED ────────────────────────────
        summary_dict = summary.model_dump()
        await self._repo.store_summary(meeting_id, summary_dict, title=title)

        logger.info(
            "[processor] Meeting %s processing COMPLETE. "
            "Segments=%d, key_points=%d, action_items=%d",
            meeting_id,
            len(transcript.segments),
            len(summary.key_points),
            len(summary.action_items),
        )

        return {
            "meeting_id": meeting_id,
            "status": MeetingStatus.COMPLETED.value,
            "message": "Meeting processed successfully",
            "segments": len(transcript.segments),
            "key_points": len(summary.key_points),
            "action_items": len(summary.action_items),
        }
