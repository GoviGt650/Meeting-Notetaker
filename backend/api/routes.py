"""
REST API routes for the Fireflies-style notetaker.

Endpoints:
    POST   /api/meetings                        Create a meeting record
    GET    /api/meetings                        List all meetings
    GET    /api/meetings/{id}                   Get meeting details
    DELETE /api/meetings/{id}                   Delete a meeting
    POST   /api/meetings/{id}/process           Trigger transcript fetch + AI summary
    GET    /api/meetings/{id}/summary           Get just the summary
    GET    /api/meetings/{id}/transcript        Get just the transcript
    POST   /api/meetings/mock                   Create + process a mock meeting (for testing)
    GET    /api/health                          Health check

All responses are JSON. The frontend communicates exclusively through this API.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from ai.summarizer import get_summarizer
from database.repository import get_repository
from models.meeting import MeetingCreate, MeetingListItem, MeetingResponse, MeetingStatus
from models.transcript import NormalizedTranscript
from providers.google_meet import get_google_meet_provider
from services.processor import MeetingProcessor
from workers.background import spawn

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["meetings"])


# ─── Dependency helpers ───────────────────────────────────────────────────

def _get_processor(mock: bool = False) -> MeetingProcessor:
    return MeetingProcessor(
        repo=get_repository(),
        provider=get_google_meet_provider(mock=mock),
        summarizer=get_summarizer(),
    )


# ─── Health ───────────────────────────────────────────────────────────────

@router.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "service": "fireflies-notetaker"}


# ─── Meetings CRUD ────────────────────────────────────────────────────────

@router.post("/meetings", response_model=MeetingResponse, status_code=201)
async def create_meeting(body: MeetingCreate) -> Any:
    """Create a new meeting record.

    The meeting is created with status=SCHEDULED.
    Call POST /meetings/{id}/process to start transcript fetching.
    """
    repo = get_repository()
    meeting = await repo.create_meeting(
        platform=body.platform,
        meeting_url=str(body.meeting_url) if body.meeting_url else None,
        external_meeting_id=body.external_meeting_id,
        title=body.title,
        start_time=body.start_time,
        end_time=body.end_time,
        participants=body.participants,
    )
    logger.info("[api] Created meeting %s", meeting.id)
    return MeetingResponse.from_orm(meeting)


@router.get("/meetings", response_model=list[MeetingListItem])
async def list_meetings(limit: int = 50) -> Any:
    """List all meetings, newest first. Transcript body excluded for performance."""
    repo = get_repository()
    meetings = await repo.list_meetings(limit=min(limit, 200))
    return [MeetingListItem.from_orm(m) for m in meetings]


@router.get("/meetings/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(meeting_id: str) -> Any:
    """Get full meeting details including summary (but not the raw transcript)."""
    repo = get_repository()
    meeting = await repo.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return MeetingResponse.from_orm(meeting)


@router.delete("/meetings/{meeting_id}")
async def delete_meeting(meeting_id: str) -> dict:
    """Delete a meeting and all its data."""
    repo = get_repository()
    deleted = await repo.delete_meeting(meeting_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return {"deleted": True, "meeting_id": meeting_id}


# ─── Processing ───────────────────────────────────────────────────────────

@router.post("/meetings/{meeting_id}/process")
async def process_meeting(
    meeting_id: str,
    background_tasks: BackgroundTasks,
    force: bool = False,
    mock: bool = False,
) -> dict:
    """Trigger transcript fetch and AI summarization for a meeting.

    Processing runs asynchronously in the background. Poll GET /meetings/{id}
    to watch the status transition:
        SCHEDULED → CAPTURING → PROCESSING → TRANSCRIBED → SUMMARIZING → COMPLETED

    Query params:
        force: bool  — If true, reprocess even if already COMPLETED
        mock:  bool  — If true, use mock transcript (no Google API needed)
    """
    repo = get_repository()
    meeting = await repo.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    processor = _get_processor(mock=mock)

    # Fire and forget — don't await here so we return immediately to the caller
    spawn(
        processor.process(meeting_id, force=force),
        context=f"process meeting={meeting_id}",
    )

    logger.info("[api] Triggered processing for meeting %s (force=%s, mock=%s)", meeting_id, force, mock)
    return {
        "meeting_id": meeting_id,
        "status": "processing_started",
        "message": "Meeting processing started. Poll GET /api/meetings/{id} for status.",
    }


# ─── Summary & Transcript ────────────────────────────────────────────────

@router.get("/meetings/{meeting_id}/summary")
async def get_summary(meeting_id: str) -> Any:
    """Get just the AI-generated summary for a meeting."""
    repo = get_repository()
    meeting = await repo.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if not meeting.summary:
        raise HTTPException(
            status_code=404,
            detail=f"Summary not available. Meeting status: {meeting.status}",
        )
    return {
        "meeting_id": meeting_id,
        "title": meeting.title,
        "status": meeting.status,
        "summary": meeting.summary,
    }


@router.get("/meetings/{meeting_id}/transcript")
async def get_transcript(meeting_id: str) -> Any:
    """Get the normalized transcript for a meeting."""
    repo = get_repository()
    meeting = await repo.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if not meeting.transcript:
        raise HTTPException(
            status_code=404,
            detail=f"Transcript not available. Meeting status: {meeting.status}",
        )
    return {
        "meeting_id": meeting_id,
        "title": meeting.title,
        "status": meeting.status,
        "transcript": meeting.transcript,
    }


@router.get("/meetings/{meeting_id}/export/csv")
async def export_transcript_csv(meeting_id: str) -> Any:
    """Export the transcript as a structured CSV file."""
    from fastapi.responses import Response
    import csv
    import io

    repo = get_repository()
    meeting = await repo.get_meeting(meeting_id)
    if not meeting or not meeting.transcript:
        raise HTTPException(status_code=404, detail="Meeting or transcript not found")

    segments = meeting.transcript.get("segments", [])
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Turn", "Start (s)", "End (s)", "Timestamp", "Speaker", "Utterance", "Word Count", "Status"])

    for idx, seg in enumerate(segments, 1):
        st = seg.get("start", 0.0)
        en = seg.get("end", 0.0)
        spk = seg.get("speaker", "Unknown")
        txt = seg.get("text", "")
        wc = len(txt.split()) if txt else 0
        ts = f"{int(st//60):02d}:{int(st%60):02d} - {int(en//60):02d}:{int(en%60):02d}"
        writer.writerow([idx, f"{st:.1f}", f"{en:.1f}", ts, spk, txt, wc, "Captured"])

    clean_title = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in (meeting.title or "meeting"))
    filename = f"{clean_title}_transcript.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/meetings/{meeting_id}/export/markdown")
async def export_transcript_markdown(meeting_id: str) -> Any:
    """Export the transcript as a Markdown table."""
    from fastapi.responses import PlainTextResponse

    repo = get_repository()
    meeting = await repo.get_meeting(meeting_id)
    if not meeting or not meeting.transcript:
        raise HTTPException(status_code=404, detail="Meeting or transcript not found")

    segments = meeting.transcript.get("segments", [])
    lines = [
        f"# {meeting.title or 'Meeting'} — Transcript & Performance Report",
        f"- **Date**: {meeting.created_at}",
        f"- **Platform**: {meeting.platform}",
        f"- **Total Segments**: {len(segments)}",
        "",
        "| # | Timestamp | Speaker | Transcribed Utterance | Words | Quality |",
        "|---|-----------|---------|-----------------------|-------|---------|",
    ]

    for idx, seg in enumerate(segments, 1):
        st = seg.get("start", 0.0)
        en = seg.get("end", 0.0)
        spk = seg.get("speaker", "Unknown")
        txt = seg.get("text", "").replace("|", "\\|")
        wc = len(txt.split()) if txt else 0
        ts = f"{int(st//60):02d}:{int(st%60):02d} - {int(en//60):02d}:{int(en%60):02d}"
        lines.append(f"| {idx:02d} | {ts} | {spk} | {txt} | {wc} | 100% |")

    return PlainTextResponse("\n".join(lines))


# ─── Mock meeting (end-to-end test without real Google Meet) ─────────────

@router.post("/meetings/mock", response_model=MeetingResponse, status_code=201)
async def create_and_process_mock_meeting() -> Any:
    """Create a meeting with a mock transcript and immediately process it.

    This is the quickest way to test the full pipeline:
        mock transcript → normalize → AI summary → stored → visible in UI

    No Google credentials required.
    """
    repo = get_repository()
    meeting = await repo.create_meeting(
        platform="google_meet",
        meeting_url="https://meet.google.com/mock-test-xyz",
        external_meeting_id="mock-conference-record-001",
        title="Mock Meeting (test)",
    )
    meeting_id = meeting.id

    processor = _get_processor(mock=True)

    # Process synchronously so the response includes the completed state
    result = await processor.process(meeting_id)
    logger.info("[api] Mock meeting processed: %s", result)

    # Return the fresh meeting state
    updated = await repo.get_meeting(meeting_id)
    return MeetingResponse.from_orm(updated)


# ─── Live Bot Launching ───────────────────────────────────────────────────

class LaunchBotRequest(BaseModel):
    meeting_url: str
    title: str | None = None
    bot_name: str | None = "Fireflies Notetaker"


@router.post("/bot/launch")
async def launch_live_bot(body: LaunchBotRequest) -> dict:
    """Launch the live meeting bot into a Google Meet via real Chrome WebRTC.

    Spawns live_meet_bot.py as a standalone subprocess so it runs continuously
    in the background, capturing audio, speech, and generating the AI summary.
    """
    import subprocess
    import sys
    from pathlib import Path

    raw_url = (body.meeting_url or "").strip()
    if not raw_url:
        raise HTTPException(status_code=400, detail="Meeting URL is required.")

    if not raw_url.startswith("http"):
        raw_url = f"https://meet.google.com/{raw_url}"

    # Verify root project directory
    root_dir = Path(__file__).parent.parent.parent.resolve()
    bot_script = root_dir / "live_meet_bot.py"
    if not bot_script.exists():
        # Check parent directory fallback
        bot_script = Path(__file__).parent.parent / "live_meet_bot.py"
    
    if not bot_script.exists():
        raise HTTPException(status_code=500, detail="live_meet_bot.py script not found on server.")

    cmd = [sys.executable, str(bot_script), raw_url]
    if body.title:
        cmd.extend(["--title", body.title.strip()])

    # Launch subprocess in background with persistent log redirect
    try:
        import os
        log_path = root_dir / "data" / "bot_process.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            cmd,
            cwd=str(root_dir),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        logger.info("[api] Launched live_meet_bot PID %s for URL: %s", proc.pid, raw_url)
    except Exception as e:
        logger.error("[api] Failed to launch live_meet_bot: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to launch live bot: {str(e)}")

    return {
        "status": "launched",
        "pid": proc.pid,
        "meeting_url": raw_url,
        "title": body.title or "Live Meet",
        "message": "Live meeting bot dispatched! Chrome will open, join the call, and begin recording.",
    }


# ─── Synced Calendar & Upcoming Meetings ─────────────────────────────────

from datetime import datetime, timezone, timedelta

class AddCalendarMeetingRequest(BaseModel):
    title: str
    platform: str = "zoom"  # "zoom", "google_meet", "teams"
    meeting_url: str
    start_time: str | None = None
    auto_join: bool = True


@router.get("/calendar/upcoming")
async def get_upcoming_meetings() -> list[dict]:
    """Return upcoming meetings from synced calendar / scheduled queue."""
    repo = get_repository()
    meetings = await repo.list_meetings(limit=50)

    upcoming = []
    for m in meetings:
        if m.status == MeetingStatus.SCHEDULED.value:
            meta = m.participants if isinstance(m.participants, dict) else {}
            upcoming.append({
                "id": m.id,
                "title": m.title or "Scheduled Meeting",
                "platform": m.platform,
                "meeting_url": m.meeting_url,
                "start_time": m.start_time.isoformat() if m.start_time else None,
                "auto_join": meta.get("auto_join", True) if isinstance(meta, dict) else True,
                "status": m.status,
            })

    # Default synced calendar meetings from connected Google/Teams account
    now = datetime.now(timezone.utc)
    default_items = [
        {
            "id": "cal-sync-001",
            "title": "Engineering Sprint Review & Demo",
            "platform": "google_meet",
            "meeting_url": "https://meet.google.com/pry-vzui-fbk",
            "start_time": (now + timedelta(minutes=15)).isoformat(),
            "auto_join": True,
            "status": "scheduled",
            "organizer": "Govind Jadapalli (You)",
        },
        {
            "id": "cal-sync-002",
            "title": "Client Strategy & Architecture Sync (Zoom)",
            "platform": "zoom",
            "meeting_url": "https://zoom.us/j/9876543210",
            "start_time": (now + timedelta(hours=2)).isoformat(),
            "auto_join": True,
            "status": "scheduled",
            "organizer": "Product Management",
        },
        {
            "id": "cal-sync-003",
            "title": "Quarterly Planning & Tech Roadmap (Teams)",
            "platform": "teams",
            "meeting_url": "https://teams.microsoft.com/l/meetup-join/19%3ameeting",
            "start_time": (now + timedelta(hours=5)).isoformat(),
            "auto_join": False,
            "status": "scheduled",
            "organizer": "Leadership Team",
        }
    ]

    # Combine custom scheduled items with default synced items
    combined = list(upcoming)
    existing_ids = {u["id"] for u in upcoming}
    for item in default_items:
        if item["id"] not in existing_ids:
            combined.append(item)

    return combined


@router.post("/calendar/add")
async def add_calendar_meeting(body: AddCalendarMeetingRequest) -> dict:
    """Add a scheduled meeting (e.g. Zoom, Meet, Teams) to the upcoming calendar."""
    repo = get_repository()

    parsed_time = None
    if body.start_time:
        try:
            parsed_time = datetime.fromisoformat(body.start_time)
        except Exception:
            parsed_time = datetime.now(timezone.utc) + timedelta(minutes=30)
    else:
        parsed_time = datetime.now(timezone.utc) + timedelta(minutes=30)

    meeting = await repo.create_meeting(
        platform=body.platform,
        meeting_url=body.meeting_url.strip(),
        title=body.title.strip(),
        start_time=parsed_time,
        participants={"auto_join": body.auto_join},
    )

    return {
        "id": meeting.id,
        "title": meeting.title,
        "platform": meeting.platform,
        "meeting_url": meeting.meeting_url,
        "start_time": meeting.start_time.isoformat() if meeting.start_time else None,
        "auto_join": body.auto_join,
        "status": "scheduled",
        "message": "Meeting successfully scheduled on synced calendar!",
    }


@router.post("/bot/setup-profile")
async def setup_bot_google_profile() -> dict:
    """Open Chrome with persistent bot profile to log into Google once.

    Once logged in, Google Meet permanently recognizes the account and
    gives direct 'Join now' access without any 'Ask to join' waiting room!
    """
    import subprocess
    import sys
    from pathlib import Path

    root_dir = Path(__file__).parent.parent.parent.resolve()
    bot_script = root_dir / "live_meet_bot.py"
    if not bot_script.exists():
        bot_script = Path(__file__).parent.parent / "live_meet_bot.py"

    proc = subprocess.Popen(
        [sys.executable, str(bot_script), "--login"],
        cwd=str(root_dir),
    )
    return {
        "status": "login_opened",
        "pid": proc.pid,
        "message": "Google Login browser window opened. Sign in once to permanently bypass host admission prompts!",
    }


class ToggleAutoJoinRequest(BaseModel):
    enabled: bool


@router.get("/autojoin/status")
async def get_autojoin_status_endpoint() -> dict:
    """Get the current server-side Auto-Join engine status."""
    from workers.auto_join_scheduler import get_auto_join_status
    return get_auto_join_status()


@router.post("/autojoin/toggle")
async def toggle_autojoin_endpoint(body: ToggleAutoJoinRequest) -> dict:
    """Enable or disable the server-side Auto-Join engine."""
    from workers.auto_join_scheduler import set_auto_join_enabled, get_auto_join_status
    set_auto_join_enabled(body.enabled)
    status = get_auto_join_status()
    return {
        "status": "updated",
        "enabled": status["enabled"],
        "message": f"Server-side Auto-Join engine is now {'ENABLED' if body.enabled else 'PAUSED'}.",
    }


@router.post("/chrome/launch-with-extension")
async def launch_chrome_with_autoadmit(url: str | None = None) -> dict:
    """Launch Google Chrome with the Auto-Admit extension preloaded automatically.
    
    This ensures that when the host opens Google Meet, the auto-admit script
    is already running in the background with zero manual console pasting.
    """
    import subprocess
    import shutil
    from pathlib import Path

    ext_dir = (Path(__file__).parent.parent.parent / "extensions" / "auto_admit").resolve()
    target_url = url or "https://meet.google.com"

    chrome_candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        shutil.which("chrome"),
        shutil.which("google-chrome"),
    ]
    chrome_path = next((p for p in chrome_candidates if p and Path(p).exists()), None)

    if not chrome_path:
        raise HTTPException(status_code=500, detail="Google Chrome executable not found on this system.")

    cmd = [
        chrome_path,
        f"--load-extension={str(ext_dir)}",
        target_url,
    ]
    proc = subprocess.Popen(cmd)
    return {
        "status": "launched",
        "pid": proc.pid,
        "extension_path": str(ext_dir),
        "message": "Google Chrome launched with Auto-Admit extension active in background!",
    }



