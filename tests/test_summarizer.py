"""
Tests for the AI summarizer module.

Covers:
- Summarizer builds correct prompts
- Fallback summary on empty transcript
- Fallback summary on LLM failure
- Action item parsing tolerates LLM shape drift
- Full summarization with mocked OpenAI client
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from models.meeting import ActionItem, MeetingSummary
from models.transcript import NormalizedTranscript, TranscriptSegment
from ai.summarizer import (
    MeetingSummarizer,
    _parse_action_items,
    _str_list,
    _fallback_summary,
    _build_user_message,
)


# ── Helper tests ──────────────────────────────────────────────────────────

class TestParseActionItems:
    def test_dict_with_all_fields(self):
        raw = [{"task": "Write tests", "owner": "Alice", "due_date": "Friday"}]
        items = _parse_action_items(raw)
        assert len(items) == 1
        assert items[0].task == "Write tests"
        assert items[0].owner == "Alice"
        assert items[0].due_date == "Friday"

    def test_string_entries(self):
        """LLM sometimes returns plain strings instead of dicts."""
        raw = ["Buy milk", "Fix the bug"]
        items = _parse_action_items(raw)
        assert len(items) == 2
        assert items[0].owner == "Unassigned"

    def test_missing_owner_defaults_to_unassigned(self):
        raw = [{"task": "Do something"}]
        items = _parse_action_items(raw)
        assert items[0].owner == "Unassigned"

    def test_alternate_field_names(self):
        """LLM may use 'action' instead of 'task'."""
        raw = [{"action": "Deploy service", "assignee": "Bob"}]
        items = _parse_action_items(raw)
        assert items[0].task == "Deploy service"
        assert items[0].owner == "Bob"

    def test_empty_list(self):
        assert _parse_action_items([]) == []
        assert _parse_action_items(None) == []

    def test_invalid_entries_skipped(self):
        raw = [None, 42, {"task": ""}, {"task": "Valid"}]
        items = _parse_action_items(raw)
        assert len(items) == 1
        assert items[0].task == "Valid"


class TestStrList:
    def test_normal_list(self):
        assert _str_list(["a", "b", "c"]) == ["a", "b", "c"]

    def test_filters_blanks(self):
        assert _str_list(["a", "", "  ", "b"]) == ["a", "b"]

    def test_non_list_returns_empty(self):
        assert _str_list("not a list") == []
        assert _str_list(None) == []


class TestFallbackSummary:
    def test_fallback_includes_participants(self, sample_normalized_transcript):
        summary = _fallback_summary(sample_normalized_transcript)
        assert "Govind J" in summary.participants
        assert "Priya Sharma" in summary.participants
        assert summary.overview  # should contain something

    def test_fallback_on_empty_transcript(self):
        t = NormalizedTranscript(meeting_id="x", platform="google_meet", segments=[])
        summary = _fallback_summary(t)
        assert isinstance(summary, MeetingSummary)


class TestBuildUserMessage:
    def test_includes_transcript_lines(self, sample_normalized_transcript):
        msg = _build_user_message(sample_normalized_transcript)
        assert "Govind J:" in msg
        assert "Priya Sharma:" in msg

    def test_empty_transcript(self):
        t = NormalizedTranscript(meeting_id="x", platform="google_meet", segments=[])
        msg = _build_user_message(t)
        assert "No transcript content" in msg


# ── MeetingSummarizer ──────────────────────────────────────────────────────

class TestMeetingSummarizer:

    def _make_openai_response(self, content: dict) -> MagicMock:
        """Create a mock OpenAI chat completion response."""
        choice = MagicMock()
        choice.message.content = json.dumps(content)
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    @pytest.mark.asyncio
    async def test_successful_summarization(self, sample_normalized_transcript):
        llm_response = {
            "overview": "The team discussed Q4 priorities.",
            "key_points": ["OAuth fix needed", "Google Meet integration"],
            "decisions": ["Use single LLM call"],
            "action_items": [
                {"task": "Fix OAuth latency", "owner": "Arjun Mehta", "due_date": "Wednesday"}
            ],
            "participants": ["Govind J", "Priya Sharma", "Arjun Mehta"],
        }

        with patch("ai.summarizer.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(
                return_value=self._make_openai_response(llm_response)
            )
            mock_openai_cls.return_value = mock_client

            summarizer = MeetingSummarizer()
            summary = await summarizer.summarize(sample_normalized_transcript)

        assert summary.overview == "The team discussed Q4 priorities."
        assert len(summary.key_points) == 2
        assert len(summary.decisions) == 1
        assert len(summary.action_items) == 1
        assert summary.action_items[0].owner == "Arjun Mehta"
        assert "Govind J" in summary.participants

    @pytest.mark.asyncio
    async def test_returns_fallback_on_openai_error(self, sample_normalized_transcript):
        from openai import OpenAIError

        with patch("ai.summarizer.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(
                side_effect=OpenAIError("Rate limit exceeded")
            )
            mock_openai_cls.return_value = mock_client

            summarizer = MeetingSummarizer()
            summary = await summarizer.summarize(sample_normalized_transcript)

        # Should return a fallback, not raise
        assert isinstance(summary, MeetingSummary)
        assert "unavailable" in summary.overview.lower() or summary.overview

    @pytest.mark.asyncio
    async def test_returns_fallback_on_json_error(self, sample_normalized_transcript):
        with patch("ai.summarizer.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            choice = MagicMock()
            choice.message.content = "this is not valid json {"
            resp = MagicMock()
            resp.choices = [choice]
            mock_client.chat.completions.create = AsyncMock(return_value=resp)
            mock_openai_cls.return_value = mock_client

            summarizer = MeetingSummarizer()
            summary = await summarizer.summarize(sample_normalized_transcript)

        assert isinstance(summary, MeetingSummary)

    @pytest.mark.asyncio
    async def test_empty_transcript_skips_llm(self):
        """An empty transcript should return a fallback without calling the LLM."""
        empty = NormalizedTranscript(meeting_id="empty-test", platform="google_meet", segments=[])

        with patch("ai.summarizer.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_openai_cls.return_value = mock_client

            summarizer = MeetingSummarizer()
            summary = await summarizer.summarize(empty)

        # LLM should NOT have been called
        mock_client.chat.completions.create.assert_not_called()
        assert isinstance(summary, MeetingSummary)
