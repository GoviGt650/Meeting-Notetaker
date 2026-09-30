"""
Abstract base class (protocol) for meeting transcript providers.

To add a new platform (Zoom, Teams, etc.):
    1. Create providers/zoom.py
    2. Subclass AbstractMeetingProvider
    3. Implement fetch_transcript()
    4. Register in providers/__init__.py

The processor (services/processor.py) depends only on this interface.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

from models.transcript import NormalizedTranscript

logger = logging.getLogger(__name__)


class TranscriptNotReadyError(Exception):
    """Raised when the transcript does not exist yet (retry later)."""


class TranscriptFetchError(Exception):
    """Raised on a non-retryable fetch failure (auth error, 404, etc.)."""


class AbstractMeetingProvider(ABC):
    """Base class for all platform-specific transcript providers.

    Subclasses must implement fetch_transcript(). Everything else in the
    application works with the NormalizedTranscript that they produce.
    """

    platform: str = "unknown"  # subclasses override this

    @abstractmethod
    async def fetch_transcript(
        self,
        meeting_id: str,
        external_meeting_id: str,
        **kwargs,
    ) -> NormalizedTranscript:
        """Fetch and normalize the transcript for a given meeting.

        Args:
            meeting_id:           Internal app meeting UUID (for the normalized output).
            external_meeting_id:  Platform-native meeting identifier.
            **kwargs:             Provider-specific options (conference_id, etc.).

        Returns:
            NormalizedTranscript — the canonical transcript for this meeting.

        Raises:
            TranscriptNotReadyError: The meeting ended but transcript is still
                                     being processed by the platform. Retry later.
            TranscriptFetchError:    Non-retryable error (bad credentials, meeting
                                     not found, no transcript enabled, etc.).
        """
        ...

    async def validate_credentials(self) -> bool:
        """Optional credential check called at startup. Returns True if OK."""
        return True
