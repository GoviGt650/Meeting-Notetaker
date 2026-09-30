"""
Google Meet transcript provider.

Uses the Google Meet REST API (conferenceRecords) to:
  1. List transcript entries for a conference
  2. Fetch individual transcript entries (with speaker + text)
  3. Normalize into the canonical NormalizedTranscript format

Google Meet REST API reference:
  https://developers.google.com/meet/api/reference/rest

Auth model:
  - OAuth 2.0 with a refresh token (offline access)
  - Requires scopes: https://www.googleapis.com/auth/meetings.space.readonly
  - Alternatively: use a mock/test transcript (see load_mock_transcript())

Architecture note:
  This module is the ONLY place that knows about Google Meet-specific APIs.
  The processor and summarizer never import from this file directly;
  they work through the AbstractMeetingProvider interface.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import get_settings
from models.transcript import NormalizedTranscript, TranscriptSegment
from providers.base import AbstractMeetingProvider, TranscriptFetchError, TranscriptNotReadyError

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
MEET_API_BASE = "https://meet.googleapis.com/v2"


def _parse_duration_to_seconds(duration_str: Optional[str]) -> float:
    """Convert ISO 8601 duration string (e.g. 'PT1H30M15S') to seconds."""
    if not duration_str:
        return 0.0
    match = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?",
        duration_str,
    )
    if not match:
        return 0.0
    hours = float(match.group(1) or 0)
    minutes = float(match.group(2) or 0)
    seconds = float(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def _parse_offset_to_seconds(offset_str: Optional[str]) -> float:
    """Convert an offset string like '0s', '12.4s', '1m30s' to float seconds."""
    if not offset_str:
        return 0.0
    offset_str = offset_str.strip()
    # Pure seconds e.g. "12.4s"
    if offset_str.endswith("s"):
        try:
            return float(offset_str[:-1])
        except ValueError:
            pass
    # Google uses ISO durations for offsets too
    return _parse_duration_to_seconds(offset_str)


def _parse_iso(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp string into datetime."""
    if not ts_str:
        return None
    try:
        clean = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean)
    except Exception:
        return None


def _parse_iso(ts_str: Optional[str]) -> Optional[datetime]:
    """Convert ISO timestamp string to datetime object."""
    if not ts_str:
        return None
    try:
        clean = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean)
    except Exception:
        return None


class GoogleMeetProvider(AbstractMeetingProvider):
    """Fetches transcripts from the Google Meet REST API.

    The Google Meet API returns transcript entries in this shape:
        {
          "name": "conferenceRecords/.../transcripts/.../entries/...",
          "participant": { "signedinUser": { "displayName": "Alice" } },
          "text": "We need to finalize the roadmap.",
          "startTime": "2026-10-01T14:00:12Z",
          "endTime":   "2026-10-01T14:00:18Z"
        }

    We normalize these into TranscriptSegment objects.

    IMPORTANT: The Meet API requires that the recording/transcript feature
    was enabled in the meeting AND the organizer accepted the data-processing
    addendum. For MVP testing, use the mock provider or load_mock_transcript().
    """

    platform = "google_meet"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._access_token: Optional[str] = None

    # ── Authentication ────────────────────────────────────────────────────

    async def _get_access_token(self) -> str:
        """Exchange refresh token for a short-lived access token."""
        settings = self._settings
        if not settings.google_client_id or not settings.google_client_secret:
            raise TranscriptFetchError(
                "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not configured. "
                "See .env.example."
            )
        if not settings.google_refresh_token:
            raise TranscriptFetchError(
                "GOOGLE_REFRESH_TOKEN is not configured. "
                "Run the OAuth flow and add the refresh token to .env."
            )

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "refresh_token": settings.google_refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        if not resp.is_success:
            raise TranscriptFetchError(
                f"Google token refresh failed {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        return data["access_token"]

    async def _get_token(self) -> str:
        if not self._access_token:
            self._access_token = await self._get_access_token()
        return self._access_token

    # ── API helpers ───────────────────────────────────────────────────────

    async def _get(self, path: str) -> dict:
        token = await self._get_token()
        url = f"{MEET_API_BASE}/{path}"
        logger.debug("GET %s", url)
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code == 404:
            raise TranscriptNotReadyError(f"Google Meet resource not found: {path}")
        if resp.status_code == 403:
            raise TranscriptFetchError(
                f"Google Meet 403 Forbidden for {path}. "
                "Ensure the meeting had transcription enabled and the OAuth "
                "token has meetings.space.readonly scope."
            )
        if not resp.is_success:
            raise TranscriptFetchError(
                f"Google Meet API error {resp.status_code} for {path}: {resp.text[:300]}"
            )
        return resp.json()

    # ── Transcript fetch ──────────────────────────────────────────────────

    async def _list_transcripts(self, conference_record_name: str) -> list[dict]:
        """List transcript objects under a conferenceRecord."""
        data = await self._get(f"{conference_record_name}/transcripts")
        return data.get("transcripts") or []

    async def _list_transcript_entries(self, transcript_name: str) -> list[dict]:
        """List all transcript entries (utterances) for a transcript resource."""
        entries: list[dict] = []
        page_token: Optional[str] = None
        while True:
            path = f"{transcript_name}/entries"
            if page_token:
                path += f"?pageToken={page_token}"
            data = await self._get(path)
            entries.extend(data.get("transcriptEntries") or [])
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return entries

    async def _find_conference_record(self, external_meeting_id: str) -> str:
        """Resolve an external meeting ID to a conferenceRecord resource name.

        The external_meeting_id can be:
        - A conference record name already: "conferenceRecords/abc-123"
        - A meeting code: "abc-def-ghi"
        - A meeting URL: "https://meet.google.com/abc-def-ghi"
        - Or "latest" to pick the most recent conference

        Returns the conferenceRecord resource name.
        """
        clean_id = external_meeting_id.strip()
        if "meet.google.com/" in clean_id:
            clean_id = clean_id.split("meet.google.com/")[-1].split("?")[0].strip()

        if clean_id.startswith("conferenceRecords/"):
            return clean_id

        # 1. Try listing conference records filtered by meeting code
        if clean_id and clean_id != "latest":
            try:
                data = await self._get(
                    f"conferenceRecords?filter=space.meetingCode%3D%22{clean_id}%22"
                )
                records = data.get("conferenceRecords") or []
                if records:
                    return records[0]["name"]
            except Exception as e:
                logger.debug("[google_meet] Filtered conferenceRecords query error: %s", e)

        # 2. Fallback: List recent conference records (default up to 10)
        try:
            data = await self._get("conferenceRecords?pageSize=10")
            records = data.get("conferenceRecords") or []
            if records:
                if clean_id and clean_id != "latest":
                    for r in records:
                        space = r.get("space", "")
                        name = r.get("name", "")
                        if clean_id in space or clean_id in name:
                            return name
                # If specific ID was not matched or user requested "latest", return most recent record
                return records[0]["name"]
        except Exception as e:
            logger.warning("[google_meet] List conferenceRecords fallback failed: %s", e)

        raise TranscriptNotReadyError(
            f"No conferenceRecord found for meeting code: '{external_meeting_id}'. "
            "Note: Google Meet transcripts typically take 1-3 minutes after the meeting ends to appear."
        )

    def _parse_speaker(self, participant: dict) -> str:
        """Extract a display name from a Meet API participant object."""
        signed_in = participant.get("signedinUser") or {}
        anon = participant.get("anonymousUser") or {}
        phone = participant.get("phoneUser") or {}
        return (
            signed_in.get("displayName")
            or anon.get("displayName")
            or phone.get("displayName")
            or "Unknown"
        )

    def _parse_entries_to_segments(
        self, entries: list[dict], start_of_meeting: Optional[datetime]
    ) -> list[TranscriptSegment]:
        """Convert Meet API transcript entries into TranscriptSegments.

        Google Meet entries have absolute ISO timestamps. We convert them to
        offsets in seconds relative to the start of the meeting.
        """
        segments: list[TranscriptSegment] = []
        t0: Optional[float] = None

        for entry in entries:
            text = (entry.get("text") or "").strip()
            if not text:
                continue

            speaker = self._parse_speaker(entry.get("participant") or {})

            # Parse start/end timestamps
            start_ts = entry.get("startTime") or entry.get("start_time")
            end_ts = entry.get("endTime") or entry.get("end_time")

            start_dt = _parse_iso(start_ts)
            end_dt = _parse_iso(end_ts)

            # Fall back to offset seconds if absolute timestamps aren't present
            start_offset_raw = entry.get("startOffset") or entry.get("start_offset")
            end_offset_raw = entry.get("endOffset") or entry.get("end_offset")

            if start_dt is not None:
                if t0 is None:
                    ref = start_of_meeting or start_dt
                    t0 = ref.timestamp()
                start_sec = start_dt.timestamp() - t0
                end_sec = (end_dt.timestamp() - t0) if end_dt else start_sec + 1.0
            elif start_offset_raw is not None:
                start_sec = _parse_offset_to_seconds(str(start_offset_raw))
                end_sec = _parse_offset_to_seconds(str(end_offset_raw)) if end_offset_raw else start_sec + 1.0
            else:
                # No timing info available — use sequential order
                start_sec = float(len(segments))
                end_sec = start_sec + 1.0

            segments.append(TranscriptSegment(
                speaker=speaker,
                start=round(max(0.0, start_sec), 3),
                end=round(max(0.0, end_sec), 3),
                text=text,
            ))

        return segments

    # ── Public interface ──────────────────────────────────────────────────

    async def fetch_transcript(
        self,
        meeting_id: str,
        external_meeting_id: str,
        start_time: Optional[datetime] = None,
        **kwargs,
    ) -> NormalizedTranscript:
        """Fetch transcript from Google Meet API and return a NormalizedTranscript.

        Args:
            meeting_id:           Internal UUID of this meeting.
            external_meeting_id:  conferenceRecord resource name or meeting code.
            start_time:           Meeting start time (used for offset calculation).

        Raises:
            TranscriptNotReadyError: Meeting ended but transcript not yet processed.
            TranscriptFetchError:    Non-retryable error.
        """
        logger.info(
            "[google_meet] Fetching transcript for meeting %s (external_id=%s)",
            meeting_id, external_meeting_id,
        )

        conference_record_name = await self._find_conference_record(external_meeting_id)
        logger.debug("[google_meet] Conference record: %s", conference_record_name)

        transcripts = await self._list_transcripts(conference_record_name)
        if not transcripts:
            raise TranscriptNotReadyError(
                f"No transcript available yet for {conference_record_name}. "
                "Transcription may not have been enabled or is still processing."
            )

        # Use the most recent transcript
        transcript_resource = transcripts[0]
        transcript_name = transcript_resource["name"]
        logger.debug("[google_meet] Using transcript: %s", transcript_name)

        entries = await self._list_transcript_entries(transcript_name)
        if not entries:
            raise TranscriptNotReadyError(
                f"Transcript {transcript_name} exists but has no entries yet."
            )

        logger.info("[google_meet] Retrieved %d transcript entries", len(entries))
        segments = self._parse_entries_to_segments(entries, start_time)

        result = NormalizedTranscript(
            meeting_id=meeting_id,
            platform=self.platform,
            segments=segments,
        )

        logger.info(
            "[google_meet] Normalized transcript: %d segments, %.1f sec",
            len(result.segments), result.duration_sec,
        )
        return result

    async def validate_credentials(self) -> bool:
        """Try to get an access token. Returns True if credentials are valid."""
        try:
            await self._get_token()
            return True
        except Exception as e:
            logger.warning("[google_meet] Credential validation failed: %s", e)
            return False


# ─── Mock / Test Provider ────────────────────────────────────────────────

class MockGoogleMeetProvider(AbstractMeetingProvider):
    """Drop-in provider that returns a hardcoded mock transcript.

    Used for:
    - Unit tests (no real meeting needed)
    - Development demos
    - End-to-end testing the full Transcript → AI → Summary pipeline

    Switch to this provider by setting GOOGLE_CLIENT_ID="" in .env and
    passing mock=True to the processor.
    """

    platform = "google_meet"

    def __init__(self, mock_data: Optional[dict] = None) -> None:
        self._mock_data = mock_data or _default_mock_data()

    async def fetch_transcript(
        self,
        meeting_id: str,
        external_meeting_id: str,
        **kwargs,
    ) -> NormalizedTranscript:
        logger.info(
            "[mock_google_meet] Returning mock transcript for meeting %s",
            meeting_id,
        )
        data = self._mock_data
        segments = [
            TranscriptSegment(
                speaker=s["speaker"],
                start=float(s["start"]),
                end=float(s["end"]),
                text=s["text"],
            )
            for s in data.get("segments", [])
        ]
        return NormalizedTranscript(
            meeting_id=meeting_id,
            platform=self.platform,
            segments=segments,
        )


def _default_mock_data() -> dict:
    """Return a realistic mock Google Meet transcript for testing."""
    return {
        "platform": "google_meet",
        "segments": [
            {"speaker": "Govind J",      "start": 0.0,   "end": 8.2,   "text": "Alright, let's get started. Today's meeting is about the Q4 roadmap and what we need to ship before the end of the quarter."},
            {"speaker": "Priya Sharma",  "start": 8.5,   "end": 20.1,  "text": "Thanks Govind. I wanted to bring up the authentication flow first. We've been getting reports that the login redirect is slow after the OAuth callback — sometimes taking 4 to 5 seconds."},
            {"speaker": "Arjun Mehta",   "start": 20.4,  "end": 34.8,  "text": "Yeah, I looked into that last week. The issue is we're doing a synchronous DB lookup for the user profile right in the request path. I can move that to a background task and return a loading state to the frontend. Should cut the perceived latency by 70 percent."},
            {"speaker": "Govind J",      "start": 35.1,  "end": 45.3,  "text": "Good find. Let's make that a priority. Arjun can you have a PR up by Wednesday?"},
            {"speaker": "Arjun Mehta",   "start": 45.6,  "end": 47.0,  "text": "Absolutely."},
            {"speaker": "Priya Sharma",  "start": 47.2,  "end": 61.5,  "text": "The second item is the notetaker integration. We decided last week to go with the Google Meet REST API over a browser bot. I've been reading the conferenceRecords documentation and the transcript API looks solid. The main limitation is that the meeting host needs to enable transcription."},
            {"speaker": "Govind J",      "start": 61.8,  "end": 75.2,  "text": "Right. That's acceptable for our initial customers since they're internal teams. Let's document that requirement clearly. Priya, can you own the Google Meet provider implementation and have a working mock by end of sprint?"},
            {"speaker": "Priya Sharma",  "start": 75.5,  "end": 78.0,  "text": "Yes, I'll have the mock done by Thursday and real API integration by next Monday."},
            {"speaker": "Arjun Mehta",   "start": 78.3,  "end": 92.7,  "text": "One thing worth discussing is the summarization model. We're currently using GPT-4o-mini via OpenRouter. For most meetings that's fine, but I worry about very long meetings going over the context window. Should we add a chunking strategy?"},
            {"speaker": "Govind J",      "start": 93.0,  "end": 108.4, "text": "Good point. Let's keep it simple for now — one call for the full transcript. If a meeting is longer than two hours we can truncate to the most recent segments. We'll add chunking as a follow-up. Add a GitHub issue for it."},
            {"speaker": "Arjun Mehta",   "start": 108.7, "end": 110.0, "text": "Done."},
            {"speaker": "Priya Sharma",  "start": 110.2, "end": 124.6, "text": "Last item: the frontend. The design looks great but I noticed the meeting detail page doesn't handle the loading state gracefully when the summary is still being generated. Users see a blank panel. We should add a spinner or a progress indicator."},
            {"speaker": "Govind J",      "start": 124.9, "end": 140.1, "text": "Agreed. Let's add a status polling mechanism — the frontend can poll every 3 seconds while status is SUMMARIZING and then refresh the summary panel once it's COMPLETED. I'll implement that in the JS layer."},
            {"speaker": "Arjun Mehta",   "start": 140.4, "end": 148.3, "text": "Makes sense. We should also think about a failure state — if the LLM call fails, what does the user see?"},
            {"speaker": "Govind J",      "start": 148.6, "end": 160.0, "text": "The API returns the error_message field. The frontend should show a friendly error banner with a retry button that calls POST /meetings/{id}/process again. That's already idempotent on the backend."},
            {"speaker": "Priya Sharma",  "start": 160.3, "end": 165.5, "text": "Perfect. I think we've covered everything. Let me summarize the action items."},
            {"speaker": "Govind J",      "start": 165.8, "end": 178.0, "text": "Go ahead."},
            {"speaker": "Priya Sharma",  "start": 178.2, "end": 210.5, "text": "Arjun will fix the OAuth latency by moving the user profile lookup to a background task, PR by Wednesday. I'll implement the Google Meet provider mock by Thursday and the real API by Monday. Govind will add the frontend polling for SUMMARIZING status. Arjun will open a GitHub issue for transcript chunking. And Govind will add the error banner with retry button to the meeting detail page."},
            {"speaker": "Govind J",      "start": 210.8, "end": 218.0, "text": "Perfect. Let's sync again on Friday. Thanks everyone."},
        ]
    }


# ─── Utility ─────────────────────────────────────────────────────────────

def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 timestamp string to an aware datetime or return None."""
    if not ts:
        return None
    try:
        # Python 3.11+ handles Z suffix
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def get_google_meet_provider(mock: bool = False) -> AbstractMeetingProvider:
    """Factory that returns either the real or mock Google Meet provider.

    Use mock=True for unit tests or when credentials are not configured.
    """
    settings = get_settings()
    if mock or not settings.google_client_id:
        logger.info("[google_meet] Using MockGoogleMeetProvider")
        return MockGoogleMeetProvider()
    return GoogleMeetProvider()
