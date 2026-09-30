"""
Normalized transcript format.

All providers (Google Meet, Zoom, Teams) produce transcripts in their own
formats (VTT, JSON, DOCVTT). The normalized format is what the rest of the
application operates on. The AI summarizer, the database, and the API all
work with NormalizedTranscript — never with raw provider output.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class TranscriptSegment:
    """A single speaker turn in the meeting transcript.

    Fields:
        speaker:  Display name of the speaker (from platform caption data).
        start:    Start offset in seconds from the beginning of the meeting.
        end:      End offset in seconds.
        text:     The spoken utterance, cleaned of STT artifacts.
    """
    speaker: str
    start: float
    end: float
    text: str


@dataclass
class NormalizedTranscript:
    """Platform-agnostic transcript container.

    All provider implementations must return this format. Downstream
    consumers (summarizer, storage, API) never import provider-specific code.

    Fields:
        meeting_id:   The application's internal meeting ID (UUID).
        platform:     Originating platform identifier, e.g. 'google_meet'.
        segments:     Ordered list of speaker turns.
        raw_text:     Optional convenience field — the full transcript as a
                      single string. Computed from segments if not provided.
        duration_sec: Total meeting duration in seconds (end of last segment).
    """
    meeting_id: str
    platform: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    raw_text: Optional[str] = None
    duration_sec: float = 0.0

    def __post_init__(self) -> None:
        # Auto-compute raw_text and duration from segments if not set
        if self.segments:
            if self.raw_text is None:
                self.raw_text = "\n".join(
                    f"{seg.speaker}: {seg.text}" for seg in self.segments
                )
            if self.duration_sec == 0.0:
                self.duration_sec = self.segments[-1].end

    def to_dict(self) -> dict:
        """Serialize to a plain dict suitable for JSON storage."""
        return {
            "meeting_id": self.meeting_id,
            "platform": self.platform,
            "duration_sec": self.duration_sec,
            "segments": [
                {
                    "speaker": seg.speaker,
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text,
                }
                for seg in self.segments
            ],
            "raw_text": self.raw_text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NormalizedTranscript":
        """Reconstruct from a stored dict."""
        segments = [
            TranscriptSegment(
                speaker=s["speaker"],
                start=float(s["start"]),
                end=float(s["end"]),
                text=s["text"],
            )
            for s in (data.get("segments") or [])
        ]
        return cls(
            meeting_id=data["meeting_id"],
            platform=data["platform"],
            segments=segments,
            raw_text=data.get("raw_text"),
            duration_sec=float(data.get("duration_sec", 0.0)),
        )
