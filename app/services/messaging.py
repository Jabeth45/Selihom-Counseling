import logging
from telegram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import Settings
from app.keyboards import (
    accept_session_keyboard,
    counselor_main_keyboard,
    student_main_keyboard,
)
from app.models import CounselingSession, User
from app.services.crisis import VERIFIED_EMERGENCY_MESSAGE_AM, evaluate_message_safety

logger = logging.getLogger(__name__)


async def broadcast_new_request_to_counselors(
    bot: Bot,
    counselors: list[User],
    session_id: int,
) -> int:
    """
    Notifies available counselors of a new anonymous request.
    Exposes ONLY the anonymous session ID—never student telegram_id or username.
    """
    sent_count = 0
    prompt_text = (
        f"🆕 **አዲስ የምክር ጥያቄ ደርሷል (ጥያቄ #{session_id})**\n\n"
        f"አንድ ተማሪ ሚስጥራዊ የምክር ውይይት ጠይቋል።\n"
        f"ለመቀበል ከታች ያለውን ቁልፍ ይጫኑ (ቀድሞ የተቀበለ አማካሪ ውይይቱን ይጀምራል)።"
    )
    for counselor in counselors:
        try:
            await bot.send_message(
                chat_id=counselor.telegram_id,
                text=prompt_text,
                parse_mode="Markdown",
                reply_markup=accept_session_keyboard(session_id),
            )
            sent_count += 1
        except Exception:
            logger.warning("Failed to notify counselor id=%s", counselor.id)
    return sent_count


async def trigger_crisis_alerts(
    bot: Bot,
    settings: Settings,
    session_id: int | None,
    role_label: str,
    fail_closed: bool = False,
) -> None:
    """
    Sends immediate alert to designated crisis chat or AMUECSF leaders.
    Does NOT include raw message transcripts to protect confidentiality,
    while alerting human leaders immediately to intervene/check in.
    """
    reason = (
        "የደህንነት ፍተሻ ስህተት (Fail-Closed Safety Trigger)"
        if fail_closed
        else "ራስን የመጉዳት / ከፍተኛ ጭንቀት ቃላት ተገኝተዋል (Crisis Phrase Detected)"
    )
    session_ref = f"ውይይት #{session_id}" if session_id else "ከውይይት ውጭ (Pre-session)"
    alert_text = (
        "🚨 **አስቸኳይ የAMUECSF የአደጋ ማስጠንቀቂያ (CRISIS ALERT)** 🚨\n\n"
        f"• **ሁኔታ:** {reason}\n"
        f"• **ምንጭ:** {role_label} ({session_ref})\n"
        "• **እርምጃ:** ለተማሪው የአስቸኳይ ጊዜ የህክምና እና የስልክ መስመሮች ወዲያውኑ ተልከዋል። "
        "እባክዎ አማካሪው እና አመራሮች ተገቢውን ክትትል ያድርጉ።"
    )

    targets: list[int] = []
    if settings.CRISIS_ALERT_CHAT_ID is not None:
        targets.append(settings.CRISIS_ALERT_CHAT_ID)
    else:
        targets.extend(settings.LEADER_IDS)

    for chat_id in set(targets):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=alert_text,
                parse_mode="Markdown",
            )
        except Exception:
            logger.error("Failed to dispatch crisis alert to chat_id=%s", chat_id)


async def relay_session_message(
    bot: Bot,
    db: AsyncSession,
    settings: Settings,
    sender: User,
    session: CounselingSession,
    text: str,
) -> tuple[bool, str | None]:
    """
    Relays a text message between matched student and counselor without persisting content.
    Runs crisis evaluation first and fails closed on crisis or error.
    """
    # 1. Crisis & safety evaluation
    evaluation = evaluate_message_safety(text)
    if evaluation.is_crisis:
        session.crisis_flagged = True
        await db.commit()

        role_label = "ተማሪ (Student)" if sender.id == session.student_id else "አማካሪ (Counselor)"
        await trigger_crisis_alerts(
            bot=bot,
            settings=settings,
            session_id=session.id,
            role_label=role_label,
            fail_closed=evaluation.fail_closed_error,
        )

        # Always deliver verified emergency numbers directly to the student
        student_res = await db.execute(select(User).where(User.id == session.student_id))
        student_user = student_res.scalar_one_or_none()
        if student_user is not None:
            await bot.send_message(
                chat_id=student_user.telegram_id,
                text=VERIFIED_EMERGENCY_MESSAGE_AM,
                parse_mode="Markdown",
            )

        if evaluation.fail_closed_error:
            return False, "⚠️ የደህንነት ፍተሻ ስህተት ስለተፈጠረ መልዕክቱ አልተላከም። እባክዎ ጽሑፍ ብቻ ይላኩ።"

    # 2. Identify recipient strictly via internal session mapping
    if sender.id == session.student_id:
        if session.counselor_id is None:
            return False, "⏳ ጥያቄዎ ገና በአማካሪ አልተወሰደም። እባክዎ ትንሽ ይጠብቁ..."
        recipient_res = await db.execute(select(User).where(User.id == session.counselor_id))
        recipient = recipient_res.scalar_one_or_none()
        prefix = f"👤 **ተማሪ #{session.id}:**\n"
    elif sender.id == session.counselor_id:
        recipient_res = await db.execute(select(User).where(User.id == session.student_id))
        recipient = recipient_res.scalar_one_or_none()
        prefix = "🕊️ **አማካሪ (Counselor):**\n"
    else:
        return False, "⛔ የዚህ ውይይት ተሳታፊ አይደሉም።"

    if recipient is None:
        return False, "⚠️ ተቀባዩ አልተገኘም። ውይይቱ ተቋርጧል።"

    # 3. Relay in-memory only; never log or save `text`
    await bot.send_message(
        chat_id=recipient.telegram_id,
        text=f"{prefix}{text}",
        parse_mode="Markdown",
    )
    return True, None


async def notify_session_closed(
    bot: Bot,
    db: AsyncSession,
    session: CounselingSession,
    closed_by_user_id: int,
) -> None:
    """
    Notifies both participants that the session ended and resets their keyboards.
    """
    student_res = await db.execute(select(User).where(User.id == session.student_id))
    student = student_res.scalar_one_or_none()

    counselor = None
    if session.counselor_id is not None:
        counselor_res = await db.execute(select(User).where(User.id == session.counselor_id))
        counselor = counselor_res.scalar_one_or_none()

    if student is not None:
        msg = (
            "✅ የምክር ውይይቱን አጠናቀዋል። መልዕክቶችዎ አልተቀመጡም።\n"
            "በማንኛውም ጊዜ አዲስ ውይይት መጀመር ይችላሉ።"
            if closed_by_user_id == student.id
            else "ℹ️ አማካሪው የምክር ውይይቱን አጠናቋል። መልዕክቶችዎ አልተቀመጡም።\n"
            "ተጨማሪ እርዳታ ከፈለጉ አዲስ ጥያቄ መላክ ይችላሉ።"
        )
        await bot.send_message(
            chat_id=student.telegram_id,
            text=msg,
            reply_markup=student_main_keyboard(has_active_session=False),
        )

    if counselor is not None:
        msg = (
            f"✅ ውይይት #{session.id} ተጠናቋል።"
            if closed_by_user_id == counselor.id
            else f"ℹ️ ተማሪ #{session.id} ውይይቱን አጠናቋል።"
        )
        await bot.send_message(
            chat_id=counselor.telegram_id,
            text=msg,
            reply_markup=counselor_main_keyboard(
                is_available=counselor.is_available,
                has_active_session=False,
            ),
        )
