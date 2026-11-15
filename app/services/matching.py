from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import CounselingSession, SessionStatus, User, UserRole


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_or_create_user(
    db: AsyncSession,
    telegram_id: int,
    leader_ids: list[int],
) -> User:
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()

    is_configured_leader = telegram_id in leader_ids

    if user is None:
        user = User(
            telegram_id=telegram_id,
            role=UserRole.LEADER if is_configured_leader else UserRole.STUDENT,
            is_approved_counselor=is_configured_leader,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    # Sync leader promotion if added to LEADER_IDS env var
    if is_configured_leader and user.role != UserRole.LEADER:
        user.role = UserRole.LEADER
        user.is_approved_counselor = True
        await db.commit()
        await db.refresh(user)

    return user


async def get_active_session_for_user(
    db: AsyncSession,
    user: User,
) -> CounselingSession | None:
    stmt = select(CounselingSession).where(
        CounselingSession.status.in_([SessionStatus.WAITING, SessionStatus.ACTIVE]),
        (CounselingSession.student_id == user.id) | (CounselingSession.counselor_id == user.id),
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_counseling_request(
    db: AsyncSession,
    student: User,
) -> tuple[CounselingSession, bool]:
    """
    Returns (session, created_new).
    Prevents duplicate open requests from the same student.
    """
    existing = await get_active_session_for_user(db, student)
    if existing is not None:
        return existing, False

    session = CounselingSession(
        student_id=student.id,
        status=SessionStatus.WAITING,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session, True


async def get_available_counselors(db: AsyncSession) -> list[User]:
    """
    Returns counselors who are approved, marked available, and not currently in an ACTIVE session.
    """
    busy_counselor_ids = select(CounselingSession.counselor_id).where(
        CounselingSession.status == SessionStatus.ACTIVE,
        CounselingSession.counselor_id.is_not(None),
    )
    stmt = select(User).where(
        User.is_approved_counselor.is_(True),
        User.is_available.is_(True),
        User.consented.is_(True),
        User.id.not_in(busy_counselor_ids),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_waiting_sessions(db: AsyncSession) -> list[CounselingSession]:
    stmt = (
        select(CounselingSession)
        .where(CounselingSession.status == SessionStatus.WAITING)
        .order_by(CounselingSession.created_at.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def accept_session_atomically(
    db: AsyncSession,
    session_id: int,
    counselor: User,
) -> tuple[bool, str, CounselingSession | None]:
    """
    Atomically claims a WAITING session for an eligible counselor.
    Uses a conditional SQL UPDATE (WHERE status = 'WAITING') inside a transaction
    so concurrent acceptances from multiple counselors resolve deterministically:
    first commit wins, all others get False.
    """
    if not counselor.is_approved_counselor or not counselor.consented:
        return False, "ፈቃድ ያለዎት አማካሪ አይደሉም ወይም መመሪያውን አልተስማሙም።", None

    # Ensure counselor is not already handling another active session
    existing_active = await db.execute(
        select(CounselingSession).where(
            CounselingSession.counselor_id == counselor.id,
            CounselingSession.status == SessionStatus.ACTIVE,
        )
    )
    if existing_active.scalar_one_or_none() is not None:
        return False, "ቀድሞውኑ በሂደት ላይ ያለ የምክር ውይይት አለዎት። መጀመሪያ እሱን ይጨርሱ።", None

    now = utcnow()
    stmt = (
        update(CounselingSession)
        .where(
            CounselingSession.id == session_id,
            CounselingSession.status == SessionStatus.WAITING,
            CounselingSession.student_id != counselor.id,
        )
        .values(
            counselor_id=counselor.id,
            status=SessionStatus.ACTIVE,
            accepted_at=now,
        )
        .returning(CounselingSession.id)
    )
    result = await db.execute(stmt)
    claimed_id = result.scalar_one_or_none()

    if claimed_id is None:
        await db.rollback()
        return False, "ይህ የምክር ጥያቄ ቀድሞውኑ በሌላ አማካሪ ተወስዷል ወይም ተዘግቷል።", None

    await db.commit()

    session_res = await db.execute(
        select(CounselingSession).where(CounselingSession.id == claimed_id)
    )
    claimed_session = session_res.scalar_one()
    return True, "ጥያቄውን በተሳካ ሁኔታ ተቀብለዋል።", claimed_session


async def close_session_for_user(
    db: AsyncSession,
    user: User,
) -> CounselingSession | None:
    session = await get_active_session_for_user(db, user)
    if session is None:
        return None

    session.status = SessionStatus.CLOSED
    session.closed_at = utcnow()
    await db.commit()
    await db.refresh(session)
    return session
