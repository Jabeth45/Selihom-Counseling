import enum
from datetime import datetime, timezone
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    STUDENT = "STUDENT"
    COUNSELOR = "COUNSELOR"
    LEADER = "LEADER"


class SessionStatus(str, enum.Enum):
    WAITING = "WAITING"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False),
        default=UserRole.STUDENT,
        nullable=False,
    )
    consented: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Counselor-specific operational flags (no personal student data stored)
    is_approved_counselor: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_alias: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    student_sessions: Mapped[list["CounselingSession"]] = relationship(
        back_populates="student",
        foreign_keys="CounselingSession.student_id",
    )
    counselor_sessions: Mapped[list["CounselingSession"]] = relationship(
        back_populates="counselor",
        foreign_keys="CounselingSession.counselor_id",
    )


class CounselingSession(Base):
    """
    Stores strictly metadata and routing pointers.
    NEVER add columns for message bodies, transcripts, or student usernames.
    """

    __tablename__ = "counseling_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    counselor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, native_enum=False),
        default=SessionStatus.WAITING,
        nullable=False,
        index=True,
    )
    crisis_flagged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    student: Mapped["User"] = relationship(
        back_populates="student_sessions", foreign_keys=[student_id]
    )
    counselor: Mapped["User | None"] = relationship(
        back_populates="counselor_sessions", foreign_keys=[counselor_id]
    )

    __table_args__ = (
        Index("ix_active_student_session", "student_id", "status"),
        Index("ix_active_counselor_session", "counselor_id", "status"),
    )
