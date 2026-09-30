"""
Tests for transcript normalization.

Verifies that:
- NormalizedTranscript correctly stores and retrieves segments
- from_dict() / to_dict() round-trips are lossless
- raw_text and duration_sec are auto-computed from segments
- The sample fixture loads correctly
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from models.transcript import NormalizedTranscript, TranscriptSegment


class TestTranscriptSegment:
    def test_segment_fields(self):
        seg = TranscriptSegment(speaker="Alice", start=1.0, end=5.5, text="Hello world")
        assert seg.speaker == "Alice"
        assert seg.start == 1.0
        assert seg.end == 5.5
        assert seg.text == "Hello world"


class TestNormalizedTranscript:

    def test_raw_text_auto_computed(self):
        t = NormalizedTranscript(
            meeting_id="m1",
            platform="google_meet",
            segments=[
                TranscriptSegment("Alice", 0.0, 2.0, "Hi"),
                TranscriptSegment("Bob",   2.5, 5.0, "Hello"),
            ],
        )
        assert t.raw_text == "Alice: Hi\nBob: Hello"

    def test_duration_auto_computed(self):
        t = NormalizedTranscript(
            meeting_id="m1",
            platform="google_meet",
            segments=[
                TranscriptSegment("Alice", 0.0,  5.0,  "First"),
                TranscriptSegment("Bob",   10.0, 30.5, "Last"),
            ],
        )
        assert t.duration_sec == 30.5

    def test_empty_transcript(self):
        t = NormalizedTranscript(meeting_id="m1", platform="google_meet", segments=[])
        assert t.raw_text is None
        assert t.duration_sec == 0.0

    def test_to_dict_round_trip(self):
        original = NormalizedTranscript(
            meeting_id="round-trip-test",
            platform="google_meet",
            segments=[
                TranscriptSegment("Speaker A", 0.0, 3.0, "First utterance"),
                TranscriptSegment("Speaker B", 3.5, 7.0, "Second utterance"),
            ],
        )
        d = original.to_dict()
        restored = NormalizedTranscript.from_dict(d)

        assert restored.meeting_id == original.meeting_id
        assert restored.platform == original.platform
        assert len(restored.segments) == 2
        assert restored.segments[0].speaker == "Speaker A"
        assert restored.segments[1].text == "Second utterance"
        assert restored.duration_sec == 7.0

    def test_from_dict_with_missing_optional_fields(self):
        """from_dict() should not crash on minimal input."""
        d = {
            "meeting_id": "minimal",
            "platform": "google_meet",
            "segments": [],
        }
        t = NormalizedTranscript.from_dict(d)
        assert t.meeting_id == "minimal"
        assert t.segments == []

    def test_fixture_loads_correctly(self, sample_transcript_dict):
        t = NormalizedTranscript.from_dict(sample_transcript_dict)
        assert t.meeting_id == "test-meeting-001"
        assert t.platform == "google_meet"
        assert len(t.segments) == 15
        assert t.duration_sec > 200.0
        # Verify first and last speakers
        assert t.segments[0].speaker == "Govind J"
        assert t.segments[-1].speaker == "Govind J"

    def test_speaker_names_in_raw_text(self):
        t = NormalizedTranscript(
            meeting_id="m1",
            platform="google_meet",
            segments=[
                TranscriptSegment("Dr. Strange", 0.0, 1.0, "Text A"),
            ],
        )
        assert "Dr. Strange: Text A" in t.raw_text
