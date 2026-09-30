"""
Tests for API endpoints.

Uses FastAPI TestClient with in-memory SQLite and mocked LLM/provider.

Covers:
- GET /api/health
- POST /api/meetings
- GET /api/meetings
- GET /api/meetings/{id}
- DELETE /api/meetings/{id}
- POST /api/meetings/{id}/process
- GET /api/meetings/{id}/summary (404 before processing, 200 after)
- GET /api/meetings/{id}/transcript
- POST /api/meetings/mock (end-to-end without real Google creds)
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


class TestHealthEndpoint:
    def test_health(self, test_app):
        res = test_app.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "notetaker" in data["service"].lower()


class TestMeetingsCRUD:
    def test_create_meeting(self, test_app):
        res = test_app.post("/api/meetings", json={
            "platform": "google_meet",
            "title": "Test Meeting",
            "external_meeting_id": "conf-001",
        })
        assert res.status_code == 201
        data = res.json()
        assert data["id"]
        assert data["status"] == "scheduled"
        assert data["platform"] == "google_meet"
        return data["id"]

    def test_create_meeting_defaults_to_google_meet(self, test_app):
        res = test_app.post("/api/meetings", json={"title": "Minimal Meeting"})
        assert res.status_code == 201
        assert res.json()["platform"] == "google_meet"

    def test_list_meetings(self, test_app):
        test_app.post("/api/meetings", json={"title": "Meeting A"})
        test_app.post("/api/meetings", json={"title": "Meeting B"})
        res = test_app.get("/api/meetings")
        assert res.status_code == 200
        assert isinstance(res.json(), list)
        assert len(res.json()) >= 2

    def test_get_meeting(self, test_app):
        create_res = test_app.post("/api/meetings", json={"title": "Fetchable"})
        meeting_id = create_res.json()["id"]

        res = test_app.get(f"/api/meetings/{meeting_id}")
        assert res.status_code == 200
        assert res.json()["id"] == meeting_id

    def test_get_meeting_not_found(self, test_app):
        res = test_app.get("/api/meetings/nonexistent-00000")
        assert res.status_code == 404

    def test_delete_meeting(self, test_app):
        create_res = test_app.post("/api/meetings", json={"title": "To Delete"})
        mid = create_res.json()["id"]

        del_res = test_app.delete(f"/api/meetings/{mid}")
        assert del_res.status_code == 200
        assert del_res.json()["deleted"] is True

        # Confirm it's gone
        assert test_app.get(f"/api/meetings/{mid}").status_code == 404

    def test_delete_nonexistent_meeting(self, test_app):
        res = test_app.delete("/api/meetings/ghost-id-00000")
        assert res.status_code == 404


class TestSummaryAndTranscript:
    def test_summary_not_available_before_processing(self, test_app):
        create_res = test_app.post("/api/meetings", json={"title": "No Summary Yet"})
        mid = create_res.json()["id"]
        res = test_app.get(f"/api/meetings/{mid}/summary")
        assert res.status_code == 404

    def test_transcript_not_available_before_processing(self, test_app):
        create_res = test_app.post("/api/meetings", json={"title": "No Transcript"})
        mid = create_res.json()["id"]
        res = test_app.get(f"/api/meetings/{mid}/transcript")
        assert res.status_code == 404


class TestProcessEndpoint:
    def test_process_returns_processing_started(self, test_app):
        create_res = test_app.post("/api/meetings", json={"title": "To Process"})
        mid = create_res.json()["id"]

        # process with mock=True so it uses MockGoogleMeetProvider
        res = test_app.post(f"/api/meetings/{mid}/process?mock=true")
        assert res.status_code == 200
        data = res.json()
        assert data["meeting_id"] == mid
        # Background task triggered, status is "processing_started"
        assert "processing" in data["status"].lower() or "started" in data["status"].lower()

    def test_process_nonexistent_meeting(self, test_app):
        res = test_app.post("/api/meetings/ghost-99999/process")
        assert res.status_code == 404


class TestMockEndpoint:
    def _make_mock_openai(self, mock_openai_cls, payload: dict):
        """Helper to set up a mock OpenAI client returning the given payload."""
        import json as _json
        from unittest.mock import MagicMock
        mock_client = AsyncMock()
        choice = MagicMock()
        choice.message.content = _json.dumps(payload)
        mock_response = MagicMock()
        mock_response.choices = [choice]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_openai_cls.return_value = mock_client
        return mock_client

    def test_mock_meeting_end_to_end(self, test_app):
        """POST /api/meetings/mock should create, process, and return COMPLETED meeting."""
        with patch("ai.summarizer.AsyncOpenAI") as mock_openai:
            self._make_mock_openai(mock_openai, {
                "overview": "Mock meeting overview.",
                "key_points": ["Point A", "Point B"],
                "decisions": ["Decision X"],
                "action_items": [{"task": "Do X", "owner": "Alice", "due_date": None}],
                "participants": ["Govind J", "Priya Sharma"],
            })
            res = test_app.post("/api/meetings/mock")

        assert res.status_code == 201
        data = res.json()
        assert data["status"] == "completed"
        assert data["has_summary"] is True
        assert data["has_transcript"] is True
        assert data["platform"] == "google_meet"

    def test_mock_meeting_has_summary_content(self, test_app):
        """The summary returned by the mock meeting should have proper structure."""
        with patch("ai.summarizer.AsyncOpenAI") as mock_openai:
            self._make_mock_openai(mock_openai, {
                "overview": "Team synced on project status.",
                "key_points": ["Auth latency fix", "Google Meet integration"],
                "decisions": ["Ship by Friday"],
                "action_items": [{"task": "Deploy hotfix", "owner": "Arjun", "due_date": "Friday"}],
                "participants": ["Govind J", "Arjun Mehta"],
            })
            res = test_app.post("/api/meetings/mock")

        assert res.status_code == 201
        summary = res.json().get("summary", {})
        assert "overview" in summary
        assert isinstance(summary.get("key_points"), list)
        assert isinstance(summary.get("action_items"), list)
