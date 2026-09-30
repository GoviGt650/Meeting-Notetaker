"""
Meeting data models.

Two layers:
1. SQLAlchemy ORM model (MeetingORM) — maps to the `meetings` table
2. Pydantic schemas — used for API request/response validation
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ─── Status State Machine ─────────────────────────────────────────────────

class MeetingStatus(str, enum.Enum):
    """Meeting processing lifecycle states.

    Transitions:
        SCHEDULED → CAPTURING → PROCESSING → TRANSCRIBED → SUMMARIZING → COMPLETED
        Any state → FAILED (on unrecoverable error)
    """
    SCHEDULED   = "scheduled"    # Meeting registered, transcript not yet fetched
    CAPTURING   = "capturing"    # Actively fetching transcript from provider
    PROCESSING  = "processing"   # Transcript fetched, normalizing
    TRANSCRIBED = "transcribed"  # Normalized transcript stored
    SUMMARIZING = "summarizing"  # LLM summarization in progress
    COMPLETED   = "completed"    # Full summary stored, ready for display
    FAILED      = "failed"       # Unrecoverable error; see error_message


# ─── SQLAlchemy ORM ───────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class MeetingORM(Base):
    """SQLAlchemy ORM model for the `meetings` table."""

    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    platform: Mapped[str] = mapped_column(String, nullable=False, default="google_meet")
    meeting_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_meeting_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default=MeetingStatus.SCHEDULED.value)
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # JSON columns — stored as serialized JSON
    participants: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # list[str]
    transcript: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)    # NormalizedTranscript as dict
    summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)       # MeetingSummary as dict

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ─── Pydantic Schemas ─────────────────────────────────────────────────────

class ActionItem(BaseModel):
    task: str
    owner: str = "Unassigned"
    due_date: Optional[str] = None


class MeetingSummary(BaseModel):
    overview: str = ""
    key_points: list[str] = []
    decisions: list[str] = []
    action_items: list[ActionItem] = []
    participants: list[str] = []


class MeetingCreate(BaseModel):
    """Request body for POST /meetings."""
    platform: str = "google_meet"
    meeting_url: Optional[str] = None
    external_meeting_id: Optional[str] = None
    title: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    participants: Optional[list[str]] = None


class MeetingResponse(BaseModel):
    """Response schema for a single meeting."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    platform: str
    meeting_url: Optional[str]
    external_meeting_id: Optional[str]
    title: Optional[str]
    status: str
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    participants: Optional[Any] = None
    has_transcript: bool = False
    has_summary: bool = False
    summary: Optional[dict]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, obj: MeetingORM) -> "MeetingResponse":
        return cls(
            id=obj.id,
            platform=obj.platform,
            meeting_url=obj.meeting_url,
            external_meeting_id=obj.external_meeting_id,
            title=obj.title,
            status=obj.status,
            start_time=obj.start_time,
            end_time=obj.end_time,
            participants=obj.participants if isinstance(obj.participants, list) else (list(obj.participants.keys()) if isinstance(obj.participants, dict) else None),
            has_transcript=obj.transcript is not None,
            has_summary=obj.summary is not None,
            summary=obj.summary,
            error_message=obj.error_message,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
        )


class MeetingListItem(BaseModel):
    """Compact meeting representation for the list view (no transcript body)."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    platform: str
    meeting_url: Optional[str]
    external_meeting_id: Optional[str]
    title: Optional[str]
    status: str
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    participants: Optional[Any] = None
    has_transcript: bool
    has_summary: bool
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, obj: MeetingORM) -> "MeetingListItem":
        return cls(
            id=obj.id,
            platform=obj.platform,
            meeting_url=obj.meeting_url,
            external_meeting_id=obj.external_meeting_id,
            title=obj.title,
            status=obj.status,
            start_time=obj.start_time,
            end_time=obj.end_time,
            participants=obj.participants,
            has_transcript=obj.transcript is not None,
            has_summary=obj.summary is not None,
            error_message=obj.error_message,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
        )
