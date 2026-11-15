import logging
from datetime import datetime, timezone
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes
from app.config import get_settings
from app.database import AsyncSessionLocal
from app.keyboards import (
    accept_session_keyboard,
    consent_keyboard,
    counselor_main_keyboard,
    student_main_keyboard,
)
from app.models import SessionStatus, User, UserRole
from app.services.crisis import VERIFIED_EMERGENCY_MESSAGE_AM, evaluate_message_safety
from app.services.matching import (
    accept_session_atomically,
    close_session_for_user,
    create_counseling_request,
    get_active_session_for_user,
    get_available_counselors,
    get_or_create_user,
    list_waiting_sessions,
)
from app.services.messaging import (
    broadcast_new_request_to_counselors,
    notify_session_closed,
    relay_session_message,
    trigger_crisis_alerts,
)

logger = logging.getLogger(__name__)

WELCOME_AND_CONSENT_AM = (
    "ሰላም! ወደ **AMUECSF ሚስጥራዊ የምክር አገልግሎት ቦት** እንኳን በደህና መጡ። 🕊️\n\n"
    "🔒 **የግላዊነት እና የደህንነት መመሪያ (Privacy & Limitations):**\n"
    "1. **ሙሉ ሚስጥራዊነት:** የቴሌግራም መለያዎ (Username ወይም User ID) ለአማካሪዎች በፍጹም አይታይም።\n"
    "2. **መልዕክቶች አይቀመጡም:** የሚጽፏቸው መልዕክቶች በዳታቤዝ ውስጥ አይቀመጡም (Zero Message Storage)።\n"
    "3. **የአገልግሎቱ ወሰን:** ይህ አገልግሎት በAMUECSF ወንድሞች እና እህቶች የሚሰጥ መንፈሳዊ እና የህብረት ድጋፍ እንጂ "
    "ሙያዊ የስነ-ልቦና ህክምና ወይም የአስቸኳይ ጊዜ አደጋ መከላከያ አይደለም።\n"
    "4. **አስቸኳይ ጊዜ:** ለህይወት አስጊ የሆነ ሁኔታ ካጋጠመዎት ወዲያውኑ ወደ `907` (አምቡላንስ) ወይም `991` ይደውሉ።\n\n"
    "ለመቀጠል እባክዎ ከታች ያለውን የስምምነት ቁልፍ ይጫኑ፦"
)


def _keyboard_for_user(user: User, has_active_session: bool):
    if user.is_approved_counselor:
        return counselor_main_keyboard(
            is_available=user.is_available,
            has_active_session=has_active_session,
        )
    return student_main_keyboard(has_active_session=has_active_session)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    settings = get_settings()
    async with AsyncSessionLocal() as db:
        user = await get_or_create_user(db, update.effective_user.id, settings.LEADER_IDS)
        if not user.consented:
            await update.message.reply_text(
                WELCOME_AND_CONSENT_AM,
                parse_mode="Markdown",
                reply_markup=consent_keyboard(),
            )
            return

        active_session = await get_active_session_for_user(db, user)
        role_note = (
            "እንኳን በደህና ተመለሱ! የአማካሪ መቆጣጠሪያ ገጽዎ ዝግጁ ነው።"
            if user.is_approved_counselor
            else "እንኳን በደህና ተመለሱ! የምክር አገልግሎት ለመጠየቅ ከታች ያለውን ቁልፍ ይጠቀሙ።"
        )
        await update.message.reply_text(
            role_note,
            reply_markup=_keyboard_for_user(user, has_active_session=active_session is not None),
        )


async def handle_consent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()

    settings = get_settings()
    action = query.data.split(":", 1)[1] if query.data and ":" in query.data else ""

    async with AsyncSessionLocal() as db:
        user = await get_or_create_user(db, update.effective_user.id, settings.LEADER_IDS)
        if action == "accept":
            user.consented = True
            user.consented_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(user)

            active_session = await get_active_session_for_user(db, user)
            await query.edit_message_text(
                "✅ ስምምነትዎ ተመዝግቧል። አሁን አገልግሎቱን በሚስጥር መጠቀም ይችላሉ።"
            )
            await context.bot.send_message(
                chat_id=user.telegram_id,
                text="ከታች ካሉት አማራጮች አንዱን ይምረጡ፦",
                reply_markup=_keyboard_for_user(user, has_active_session=active_session is not None),
            )
        else:
            await query.edit_message_text(
                "❌ ያለ ስምምነት አገልግሎቱን መጠቀም አይቻልም። ሃሳብዎን ከቀየሩ /start ይላኩ።"
            )


async def _ensure_consented(update: Update, db, user: User) -> bool:
    if user.consented:
        return True
    if update.message:
        await update.message.reply_text(
            "⚠️ አገልግሎቱን ከመጠቀምዎ በፊት መጀመሪያ የግላዊነት መመሪያውን ማንበብ እና መስማማት አለብዎት።",
            reply_markup=consent_keyboard(),
        )
    elif update.callback_query:
        await update.callback_query.answer(
            "መጀመሪያ /start በመላክ መመሪያውን ይስማሙ።", show_alert=True
        )
    return False


async def request_counseling_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    settings = get_settings()
    async with AsyncSessionLocal() as db:
        user = await get_or_create_user(db, update.effective_user.id, settings.LEADER_IDS)
        if not await _ensure_consented(update, db, user):
            return

        if user.is_approved_counselor:
            await update.message.reply_text(
                "ℹ️ እርስዎ እንደ አማካሪ ተመዝግበዋል። የተማሪ ጥያቄዎችን ለመቀበል 'ዝግጁ ነኝ' የሚለውን ይጫኑ።"
            )
            return

        session, created = await create_counseling_request(db, user)
        if not created:
            status_text = (
                "አማካሪ እስኪቀበልዎ በመጠባበቅ ላይ ነዎት።"
                if session.status == SessionStatus.WAITING
                else "ቀድሞውኑ ከአማካሪ ጋር በውይይት ላይ ነዎት። መልዕክትዎን መጻፍ ይችላሉ።"
            )
            await update.message.reply_text(
                f"⏳ ጥያቄ #{session.id} ክፍት ነው፦ {status_text}",
                reply_markup=student_main_keyboard(has_active_session=True),
            )
            return

        counselors = await get_available_counselors(db)
        notified = await broadcast_new_request_to_counselors(
            bot=context.bot,
            counselors=counselors,
            session_id=session.id,
        )

        if notified > 0:
            msg = (
                f"✅ **ጥያቄዎ ተልኳል (ጥያቄ #{session.id})**\n\n"
                "ዝግጁ ለሆኑ አማካሪዎች ማሳወቂያ ተልኳል። አንድ አማካሪ ሲቀበልዎ ወዲያውኑ እናሳውቅዎታለን።\n"
                "🔒 ማንነትዎ ሙሉ በሙሉ ሚስጥራዊ ነው።"
            )
        else:
            msg = (
                f"⏳ **ጥያቄዎ ተመዝግቧል (ጥያቄ #{session.id})**\n\n"
                "በአሁኑ ሰዓት መስመር ላይ ያለ አማካሪ የለም፤ ነገር ግን ጥያቄዎ ወረፋ ላይ ተይዟል። "
                "አማካሪ ሲገባ ወዲያውኑ ያገኝዎታል።\n"
                "አስቸኳይ እርዳታ ከፈለጉ 'የአደጋ ጊዜ እርዳታ' የሚለውን ይጫኑ።"
            )

        await update.message.reply_text(
            msg,
            parse_mode="Markdown",
            reply_markup=student_main_keyboard(has_active_session=True),
        )


async def handle_session_accept_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None or query.data is None:
        return

    parts = query.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await query.answer("ልክ ያልሆነ ጥያቄ።", show_alert=True)
        return

    session_id = int(parts[2])
    settings = get_settings()

    async with AsyncSessionLocal() as db:
        counselor = await get_or_create_user(db, update.effective_user.id, settings.LEADER_IDS)
        if not await _ensure_consented(update, db, counselor):
            return

        success, reason, session = await accept_session_atomically(db, session_id, counselor)
        await query.answer(reason, show_alert=not success)

        if not success or session is None:
            try:
                await query.edit_message_text(f"ℹ️ {reason}")
            except Exception:
                pass
            return

        # Fetch student strictly to send Telegram notification (never expose student info to counselor)
        student_res = await db.execute(select(User).where(User.id == session.student_id))
        student = student_res.scalar_one()

        await query.edit_message_text(
            f"✅ **ጥያቄ #{session.id} ተቀብለዋል።**\n"
            f"አሁን ከ **ተማሪ #{session.id}** ጋር ተገናኝተዋል። መልዕክት መላክ ይችላሉ።",
            parse_mode="Markdown",
        )
        await context.bot.send_message(
            chat_id=counselor.telegram_id,
            text=f"🕊️ ከ **ተማሪ #{session.id}** ጋር ውይይት ተጀምሯል። ውይይቱን ለመጨረስ 'ውይይቱን ጨርስ' ይጫኑ።",
            parse_mode="Markdown",
            reply_markup=counselor_main_keyboard(
                is_available=counselor.is_available,
                has_active_session=True,
            ),
        )
        await context.bot.send_message(
            chat_id=student.telegram_id,
            text=(
                f"✅ **አማካሪ ተገኝቷል! (ውይይት #{session.id})**\n\n"
                "አሁን ሃሳብዎን በነፃነት እና በሚስጥር መጻፍ ይችላሉ።\n"
                "ውይይቱን ሲጨርሱ 'ውይይቱን ጨርስ' የሚለውን ይጫኑ።"
            ),
            parse_mode="Markdown",
            reply_markup=student_main_keyboard(has_active_session=True),
        )


async def toggle_availability_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    settings = get_settings()
    async with AsyncSessionLocal() as db:
        user = await get_or_create_user(db, update.effective_user.id, settings.LEADER_IDS)
        if not await _ensure_consented(update, db, user):
            return

        if not user.is_approved_counselor:
            await update.message.reply_text("⛔ ይህ ትዕዛዝ ለተመዘገቡ አማካሪዎች ብቻ የተፈቀደ ነው።")
            return

        user.is_available = not user.is_available
        await db.commit()
        await db.refresh(user)

        active_session = await get_active_session_for_user(db, user)
        status_msg = (
            "🟢 አሁን ለምክር አገልግሎት **ዝግጁ (Available)** ነዎት። አዲስ ጥያቄ ሲመጣ ማሳወቂያ ይደርስዎታል።"
            if user.is_available
            else "🔴 አሁን **ከመስመር ውጭ (Offline)** ነዎት። አዲስ ጥያቄዎች አይላኩልዎትም።"
        )
        await update.message.reply_text(
            status_msg,
            parse_mode="Markdown",
            reply_markup=counselor_main_keyboard(
                is_available=user.is_available,
                has_active_session=active_session is not None,
            ),
        )


async def list_waiting_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    settings = get_settings()
    async with AsyncSessionLocal() as db:
        user = await get_or_create_user(db, update.effective_user.id, settings.LEADER_IDS)
        if not await _ensure_consented(update, db, user):
            return

        if not user.is_approved_counselor:
            await update.message.reply_text("⛔ ይህ ትዕዛዝ ለአማካሪዎች ብቻ ነው።")
            return

        waiting = await list_waiting_sessions(db)
        if not waiting:
            await update.message.reply_text("📭 በአሁኑ ሰዓት በመጠባበቅ ላይ ያለ የምክር ጥያቄ የለም።")
            return

        await update.message.reply_text(
            f"📋 በመጠባበቅ ላይ ያሉ ጥያቄዎች ብዛት፦ **{len(waiting)}**",
            parse_mode="Markdown",
        )
        for sess in waiting[:5]:
            await update.message.reply_text(
                f"⏳ **ጥያቄ #{sess.id}** — አማካሪ በመጠባበቅ ላይ",
                parse_mode="Markdown",
                reply_markup=accept_session_keyboard(sess.id),
            )


async def end_session_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    settings = get_settings()
    async with AsyncSessionLocal() as db:
        user = await get_or_create_user(db, update.effective_user.id, settings.LEADER_IDS)
        closed_session = await close_session_for_user(db, user)
        if closed_session is None:
            await update.message.reply_text(
                "ℹ️ በአሁኑ ሰዓት ክፍት የሆነ ውይይት የለዎትም።",
                reply_markup=_keyboard_for_user(user, has_active_session=False),
            )
            return

        await notify_session_closed(
            bot=context.bot,
            db=db,
            session=closed_session,
            closed_by_user_id=user.id,
        )


async def emergency_help_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        VERIFIED_EMERGENCY_MESSAGE_AM,
        parse_mode="Markdown",
    )


async def about_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "ℹ️ **ስለ AMUECSF ሚስጥራዊ የምክር አገልግሎት**\n\n"
        "ይህ ቦት ለአርባ ምንጭ ዩኒቨርሲቲ ወንጌላውያን ክርስቲያን ተማሪዎች ህብረት (AMUECSF) አባላት "
        "ሚስጥራዊ የጸሎት እና የምክር ድጋፍ ለመስጠት የተዘጋጀ ነው።\n\n"
        "• ማንነትዎ ለአማካሪዎች አይታይም።\n"
        "• የሚጻፉ መልዕክቶች በዳታቤዝ ውስጥ አይቀመጡም።\n"
        "• አገልግሎቱ የህብረት ድጋፍ እንጂ የህክምና ወይም የአስቸኳይ ጊዜ አገልግሎት አይደለም።",
        parse_mode="Markdown",
    )


# --- Leader Administration Commands ---


def _parse_target_telegram_id(args: list[str] | None) -> int | None:
    if not args or len(args) != 1:
        return None
    raw = args[0].strip()
    if not raw.isdigit():
        return None
    return int(raw)


async def add_counselor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    settings = get_settings()
    async with AsyncSessionLocal() as db:
        caller = await get_or_create_user(db, update.effective_user.id, settings.LEADER_IDS)
        if caller.role != UserRole.LEADER:
            await update.message.reply_text("⛔ ይህ ትዕዛዝ ለAMUECSF አመራሮች (Leaders) ብቻ የተፈቀደ ነው።")
            return

        target_tg_id = _parse_target_telegram_id(context.args)
        if target_tg_id is None:
            await update.message.reply_text(
                "⚠️ ትክክለኛ የቴሌግራም መለያ ቁጥር ያስገቡ።\nምሳሌ፦ `/add_counselor 123456789`",
                parse_mode="Markdown",
            )
            return

        target_user = await get_or_create_user(db, target_tg_id, settings.LEADER_IDS)
        if target_user.role != UserRole.LEADER:
            target_user.role = UserRole.COUNSELOR
        target_user.is_approved_counselor = True
        await db.commit()

        await update.message.reply_text(
            f"✅ ተጠቃሚ `{target_tg_id}` እንደ አማካሪ (Counselor) ተመዝግቧል።\n"
            "ቦቱን /start ብሎ በመክፈት መመሪያውን ሲስማማ አገልግሎት መስጠት ይችላል።",
            parse_mode="Markdown",
        )


async def remove_counselor_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    settings = get_settings()
    async with AsyncSessionLocal() as db:
        caller = await get_or_create_user(db, update.effective_user.id, settings.LEADER_IDS)
        if caller.role != UserRole.LEADER:
            await update.message.reply_text("⛔ ይህ ትዕዛዝ ለAMUECSF አመራሮች (Leaders) ብቻ የተፈቀደ ነው።")
            return

        target_tg_id = _parse_target_telegram_id(context.args)
        if target_tg_id is None:
            await update.message.reply_text(
                "⚠️ ትክክለኛ የቴሌግራም መለያ ቁጥር ያስገቡ።\nምሳሌ፦ `/remove_counselor 123456789`",
                parse_mode="Markdown",
            )
            return

        result = await db.execute(select(User).where(User.telegram_id == target_tg_id))
        target_user = result.scalar_one_or_none()
        if target_user is None:
            await update.message.reply_text("ℹ️ ተጠቃሚው በሲስተሙ ውስጥ አልተገኘም።")
            return

        target_user.is_approved_counselor = False
        target_user.is_available = False
        if target_user.role == UserRole.COUNSELOR:
            target_user.role = UserRole.STUDENT
        await db.commit()

        await update.message.reply_text(
            f"✅ ተጠቃሚ `{target_tg_id}` ከአማካሪነት ተነስቷል።",
            parse_mode="Markdown",
        )


async def list_counselors_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    settings = get_settings()
    async with AsyncSessionLocal() as db:
        caller = await get_or_create_user(db, update.effective_user.id, settings.LEADER_IDS)
        if caller.role != UserRole.LEADER:
            await update.message.reply_text("⛔ ይህ ትዕዛዝ ለAMUECSF አመራሮች (Leaders) ብቻ የተፈቀደ ነው።")
            return

        result = await db.execute(
            select(User).where(User.is_approved_counselor.is_(True)).order_by(User.id.asc())
        )
        counselors = list(result.scalars().all())
        if not counselors:
            await update.message.reply_text("📭 የተመዘገቡ አማካሪዎች የሉም።")
            return

        lines = ["📋 **የተመዘገቡ አማካሪዎች ዝርዝር:**\n"]
        for c in counselors:
            status = "🟢 ዝግጁ" if c.is_available else "🔴 ከመስመር ውጭ"
            consent = "✅" if c.consented else "⏳ አልተስማማም"
            lines.append(f"• ID: `{c.telegram_id}` | {status} | ስምምነት: {consent}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# --- Incoming Text Router ---


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None or not update.message.text:
        return

    text = update.message.text.strip()
    if not text:
        return

    # Route known reply-keyboard actions first
    if text.startswith("🤝"):
        await request_counseling_action(update, context)
        return
    if text.startswith("🛑"):
        await end_session_action(update, context)
        return
    if text.startswith("🟢") or text.startswith("🔴"):
        await toggle_availability_action(update, context)
        return
    if text.startswith("📋"):
        await list_waiting_action(update, context)
        return
    if text.startswith("🆘"):
        await emergency_help_action(update, context)
        return
    if text.startswith("ℹ️"):
        await about_action(update, context)
        return

    settings = get_settings()
    async with AsyncSessionLocal() as db:
        user = await get_or_create_user(db, update.effective_user.id, settings.LEADER_IDS)
        if not await _ensure_consented(update, db, user):
            return

        session = await get_active_session_for_user(db, user)
        if session is None:
            # Even outside an active session, check for crisis language and respond immediately
            evaluation = evaluate_message_safety(text)
            if evaluation.is_crisis:
                await trigger_crisis_alerts(
                    bot=context.bot,
                    settings=settings,
                    session_id=None,
                    role_label="ተጠቃሚ (User)",
                    fail_closed=evaluation.fail_closed_error,
                )
                await update.message.reply_text(
                    VERIFIED_EMERGENCY_MESSAGE_AM,
                    parse_mode="Markdown",
                )
                return

            await update.message.reply_text(
                "ℹ️ በአሁኑ ሰዓት በምክር ውይይት ውስጥ አይደሉም።\n"
                "የምክር አገልግሎት ለመጀመር '🤝 የምክር አገልግሎት ጠይቅ' የሚለውን ይጫኑ።",
                reply_markup=_keyboard_for_user(user, has_active_session=False),
            )
            return

        # Session is either WAITING or ACTIVE
        if session.status == SessionStatus.WAITING:
            evaluation = evaluate_message_safety(text)
            if evaluation.is_crisis:
                session.crisis_flagged = True
                await db.commit()
                await trigger_crisis_alerts(
                    bot=context.bot,
                    settings=settings,
                    session_id=session.id,
                    role_label="ተማሪ (Student)",
                    fail_closed=evaluation.fail_closed_error,
                )
                await update.message.reply_text(
                    VERIFIED_EMERGENCY_MESSAGE_AM,
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    "⏳ ጥያቄዎ በአማካሪ እስኪቀበል ድረስ እባክዎ በትዕግስት ይጠብቁ። "
                    "አማካሪ ሲቀበልዎት ውይይቱ ወዲያውኑ ይጀምራል።",
                    reply_markup=student_main_keyboard(has_active_session=True),
                )
            return

        if session.status == SessionStatus.ACTIVE:
            success, err = await relay_session_message(
                bot=context.bot,
                db=db,
                settings=settings,
                sender=user,
                session=session,
                text=text,
            )
            if not success and err:
                await update.message.reply_text(err)
            return
