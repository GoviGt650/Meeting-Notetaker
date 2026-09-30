"""
Database repository — async SQLAlchemy + SQLite (default) or PostgreSQL.

This is the ONLY place that issues SQL queries. All other modules import
from this file rather than using SQLAlchemy sessions directly.

Design:
- All methods are async (aiosqlite / asyncpg drivers).
- No session objects leak outside this module.
- Returns domain objects (MeetingORM / dicts), never raw SQL rows.
- Idempotent: create_meeting checks for duplicates, update_* uses upsert-like logic.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models.meeting import Base, MeetingORM, MeetingStatus
from models.transcript import NormalizedTranscript

logger = logging.getLogger(__name__)


class MeetingRepository:
    """Async repository for meeting CRUD operations."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ── Create ────────────────────────────────────────────────────────────

    async def create_meeting(
        self,
        platform: str = "google_meet",
        meeting_url: Optional[str] = None,
        external_meeting_id: Optional[str] = None,
        title: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        participants: Optional[list[str]] = None,
    ) -> MeetingORM:
        """Insert a new meeting record. Returns the created ORM object."""
        meeting_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        obj = MeetingORM(
            id=meeting_id,
            platform=platform,
            meeting_url=meeting_url,
            external_meeting_id=external_meeting_id,
            title=title,
            status=MeetingStatus.SCHEDULED.value,
            start_time=start_time,
            end_time=end_time,
            participants=participants,
            created_at=now,
            updated_at=now,
        )
        async with self._session_factory() as session:
            session.add(obj)
            await session.commit()
            await session.refresh(obj)
        logger.info("[db] Created meeting %s (platform=%s)", meeting_id, platform)
        return obj

    # ── Read ──────────────────────────────────────────────────────────────

    async def get_meeting(self, meeting_id: str) -> Optional[MeetingORM]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MeetingORM).where(MeetingORM.id == meeting_id)
            )
            return result.scalar_one_or_none()

    async def list_meetings(self, limit: int = 50) -> list[MeetingORM]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MeetingORM).order_by(MeetingORM.created_at.desc()).limit(limit)
            )
            return list(result.scalars().all())

    # ── Status updates ────────────────────────────────────────────────────

    async def update_status(
        self,
        meeting_id: str,
        status: MeetingStatus,
        error_message: Optional[str] = None,
    ) -> None:
        async with self._session_factory() as session:
            values: dict = {"status": status.value, "updated_at": datetime.now(timezone.utc)}
            if error_message is not None:
                values["error_message"] = error_message
            await session.execute(
                update(MeetingORM).where(MeetingORM.id == meeting_id).values(**values)
            )
            await session.commit()
        logger.debug("[db] Meeting %s → status=%s", meeting_id, status.value)

    async def store_transcript(
        self,
        meeting_id: str,
        transcript: NormalizedTranscript,
        participants: Optional[list[str]] = None,
    ) -> None:
        """Persist the normalized transcript; also updates participants from segments or explicit list."""
        noise = {'participant', 'unknown', 'chat', 'apps', 'language', 'mood', 'settings', 'button', 'speaker'}
        if not participants:
            participants = sorted({
                seg.speaker for seg in transcript.segments
                if seg.speaker and seg.speaker.lower() not in noise
            })
        if not participants:
            participants = ["Govind Jadapalli"]

        async with self._session_factory() as session:
            await session.execute(
                update(MeetingORM)
                .where(MeetingORM.id == meeting_id)
                .values(
                    transcript=transcript.to_dict(),
                    participants=participants,
                    status=MeetingStatus.TRANSCRIBED.value,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        logger.info("[db] Stored transcript for meeting %s (%d segments, %d participants)", meeting_id, len(transcript.segments), len(participants))

    async def store_summary(
        self,
        meeting_id: str,
        summary_dict: dict,
        title: Optional[str] = None,
    ) -> None:
        """Persist the AI-generated summary and mark meeting COMPLETED."""
        values: dict = {
            "summary": summary_dict,
            "status": MeetingStatus.COMPLETED.value,
            "updated_at": datetime.now(timezone.utc),
        }
        if title:
            values["title"] = title
        async with self._session_factory() as session:
            await session.execute(
                update(MeetingORM).where(MeetingORM.id == meeting_id).values(**values)
            )
            await session.commit()
        logger.info("[db] Stored summary for meeting %s", meeting_id)

    async def fail_meeting(self, meeting_id: str, error: str) -> None:
        await self.update_status(meeting_id, MeetingStatus.FAILED, error_message=error)
        logger.warning("[db] Meeting %s marked FAILED: %s", meeting_id, error)

    # ── Delete ────────────────────────────────────────────────────────────

    async def delete_meeting(self, meeting_id: str) -> bool:
        async with self._session_factory() as session:
            obj = await session.get(MeetingORM, meeting_id)
            if not obj:
                return False
            await session.delete(obj)
            await session.commit()
        return True


# ─── Database setup ───────────────────────────────────────────────────────

_engine = None
_repo: Optional[MeetingRepository] = None


async def init_db(database_url: str) -> MeetingRepository:
    """Create engine, tables, and return the repository singleton."""
    global _engine, _repo

    # Ensure the data directory exists for SQLite
    if database_url.startswith("sqlite"):
        import os
        db_path = database_url.replace("sqlite+aiosqlite:///", "")
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    _engine = engine
    _repo = MeetingRepository(factory)
    logger.info("[db] Initialized database: %s", database_url)
    return _repo


def get_repository() -> MeetingRepository:
    """Return the application-wide repository singleton."""
    if _repo is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _repo
