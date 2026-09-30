"""
FastAPI application entry point.

Starts the server with:
    uvicorn main:app --reload       (development)
    uvicorn main:app --host 0.0.0.0 --port 8000   (production)

Architecture:
    main.py → api/routes.py → services/processor.py
                             → providers/google_meet.py
                             → ai/summarizer.py
                             → database/repository.py
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import get_settings
from database.repository import init_db
from api.routes import router

# ─── Logging ──────────────────────────────────────────────────────────────

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s │ %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ─── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB and background workers on startup; clean up on shutdown."""
    from workers.auto_join_scheduler import start_auto_join_scheduler, stop_auto_join_scheduler

    logger.info("🚀 Fireflies Notetaker starting up...")
    await init_db(settings.database_url)
    logger.info("✅ Database ready")

    # Start background auto-join engine
    start_auto_join_scheduler()
    logger.info("🤖 Auto-Join Background Scheduler active")

    logger.info("📡 API available at http://localhost:8000/api")
    logger.info("🖥️  Frontend at http://localhost:8000")
    yield
    stop_auto_join_scheduler()
    logger.info("🛑 Shutting down Fireflies Notetaker")


# ─── App ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Fireflies Notetaker",
    description=(
        "A Fireflies.ai-style meeting notetaker. "
        "Fetches Google Meet transcripts, generates AI summaries, and presents "
        "them in a clean UI."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow the frontend origin in development
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── API routes ───────────────────────────────────────────────────────────

app.include_router(router)

# ─── Frontend static files ───────────────────────────────────────────────

_FRONTEND = Path(__file__).parent.parent / "frontend"


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(_FRONTEND / "index.html")


@app.get("/meeting/{meeting_id}", include_in_schema=False)
async def meeting_detail(meeting_id: str):
    return FileResponse(_FRONTEND / "meeting.html")


# Mount all other static assets (css/, js/, etc.)
if _FRONTEND.exists():
    app.mount("/css", StaticFiles(directory=str(_FRONTEND / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(_FRONTEND / "js")), name="js")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
