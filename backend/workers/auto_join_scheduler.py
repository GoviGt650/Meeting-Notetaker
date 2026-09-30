"""
Server-Side Auto-Join Scheduler.

Continuously monitors scheduled meetings in the database and synced calendar.
When a meeting's scheduled start time arrives (or is within 45 seconds), it
automatically dispatches live_meet_bot.py in the background.

Runs as an asynchronous background loop inside the FastAPI lifespan.
"""

import asyncio
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Set

from config import get_settings
from database.repository import get_repository
from models.meeting import MeetingStatus

logger = logging.getLogger(__name__)

# State
_scheduler_running = False
_scheduler_task: asyncio.Task | None = None
_dispatched_meeting_ids: Set[str] = set()
_auto_join_enabled: bool = True


async def _run_scheduler_loop():
    """Background polling loop executing every 10 seconds."""
    global _scheduler_running
    logger.info("[Auto-Join] Background Auto-Join engine initialized and running.")
    root_dir = Path(__file__).parent.parent.parent.resolve()
    bot_script = root_dir / "live_meet_bot.py"
    log_dir = root_dir / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file_path = log_dir / "bot_process.log"

    while _scheduler_running:
        try:
            if not _auto_join_enabled:
                await asyncio.sleep(10)
                continue

            repo = get_repository()
            # Fetch scheduled meetings from DB
            scheduled_meetings = await repo.list_meetings(limit=100)
            now = datetime.now(timezone.utc)

            for m in scheduled_meetings:
                if m.status != MeetingStatus.SCHEDULED.value:
                    continue
                if m.id in _dispatched_meeting_ids:
                    continue

                meta = m.participants if isinstance(m.participants, dict) else {}
                auto_join = meta.get("auto_join", True) if isinstance(meta, dict) else True
                if not auto_join:
                    continue

                meeting_url = (m.meeting_url or "").strip()
                if not meeting_url:
                    continue

                start_time = m.start_time
                if not start_time:
                    continue

                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)

                diff_seconds = (start_time - now).total_seconds()

                # Trigger if meeting starts within 45 seconds or started up to 10 minutes ago
                if -600 <= diff_seconds <= 45:
                    _dispatched_meeting_ids.add(m.id)
                    logger.info(
                        "[Auto-Join] Meeting '%s' is starting now! Dispatching live bot for URL: %s",
                        m.title, meeting_url
                    )

                    cmd = [sys.executable, str(bot_script), meeting_url]
                    if m.title:
                        cmd.extend(["--title", m.title.strip()])

                    try:
                        out_log = open(log_file_path, "a", encoding="utf-8")
                        env = os.environ.copy()
                        env["PYTHONIOENCODING"] = "utf-8"
                        proc = subprocess.Popen(
                            cmd,
                            cwd=str(root_dir),
                            stdout=out_log,
                            stderr=subprocess.STDOUT,
                            env=env,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                        )
                        logger.info(
                            "[Auto-Join] Live bot launched with PID %s for scheduled meeting '%s'",
                            proc.pid, m.title
                        )
                        await repo.update_status(m.id, MeetingStatus.CAPTURING)
                    except Exception as launch_err:
                        logger.error("[Auto-Join] Failed to launch bot subprocess: %s", launch_err)

        except Exception as err:
            logger.error("[Auto-Join] Error in scheduler loop: %s", err)

        await asyncio.sleep(10)


def start_auto_join_scheduler():
    """Start the background scheduler task."""
    global _scheduler_running, _scheduler_task
    if _scheduler_running:
        return
    _scheduler_running = True
    _scheduler_task = asyncio.create_task(_run_scheduler_loop())


def stop_auto_join_scheduler():
    """Stop the background scheduler task."""
    global _scheduler_running, _scheduler_task
    _scheduler_running = False
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()


def get_auto_join_status() -> dict:
    """Return status of the Auto-Join engine."""
    return {
        "enabled": _auto_join_enabled,
        "scheduler_running": _scheduler_running,
        "dispatched_count": len(_dispatched_meeting_ids),
        "dispatched_ids": list(_dispatched_meeting_ids),
    }


def set_auto_join_enabled(enabled: bool):
    """Enable or disable Auto-Join globally."""
    global _auto_join_enabled
    _auto_join_enabled = enabled
