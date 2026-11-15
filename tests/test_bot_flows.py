import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy import select
from app.models import CounselingSession, SessionStatus, User, UserRole
from app.services.crisis import (
    VERIFIED_EMERGENCY_MESSAGE_AM,
    evaluate_message_safety,
)
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
from app.handlers import (
    add_counselor_command,
    handle_consent_callback,
    list_counselors_command,
    remove_counselor_command,
    _ensure_consented,
)


# ============================================================================
# 1. Consent Enforcement Tests
# ============================================================================


@pytest.mark.asyncio
async def test_consent_enforcement_blocks_unconsented_user(db_session, test_settings):
    student = await get_or_create_user(db_session, 1001, test_settings.LEADER_IDS)
    assert student.consented is False

    mock_update = MagicMock()
    mock_update.message.reply_text = AsyncMock()
    mock_update.callback_query = None

    consented = await _ensure_consented(mock_update, db_session, student)
    assert consented is False
    mock_update.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_consent_callback_accept_and_decline(db_session, test_settings):
    # Test Decline
    user_declined = await get_or_create_user(db_session, 1002, test_settings.LEADER_IDS)
    mock_update_dec = MagicMock()
    mock_update_dec.effective_user.id = user_declined.telegram_id
    mock_update_dec.callback_query.data = "consent:decline"
    mock_update_dec.callback_query.answer = AsyncMock()
    mock_update_dec.callback_query.edit_message_text = AsyncMock()
    mock_context = MagicMock()

    await handle_consent_callback(mock_update_dec, mock_context)
    await db_session.refresh(user_declined)
    assert user_declined.consented is False

    # Test Accept
    user_accepted = await get_or_create_user(db_session, 1003, test_settings.LEADER_IDS)
    mock_update_acc = MagicMock()
    mock_update_acc.effective_user.id = user_accepted.telegram_id
    mock_update_acc.callback_query.data = "consent:accept"
    mock_update_acc.callback_query.answer = AsyncMock()
    mock_update_acc.callback_query.edit_message_text = AsyncMock()
    mock_context.bot.send_message = AsyncMock()

    await handle_consent_callback(mock_update_acc, mock_context)
    await db_session.refresh(user_accepted)
    assert user_accepted.consented is True
    assert user_accepted.consented_at is not None


# ============================================================================
# 2. Anonymous Identity Protection Tests
# ============================================================================


@pytest.mark.asyncio
async def test_anonymous_identity_protection_in_broadcast(db_session, test_settings, mock_bot):
    student = await get_or_create_user(db_session, 2001, test_settings.LEADER_IDS)
    student.consented = True
    await db_session.commit()

    counselor = await get_or_create_user(db_session, 2002, test_settings.LEADER_IDS)
    counselor.role = UserRole.COUNSELOR
    counselor.is_approved_counselor = True
    counselor.is_available = True
    counselor.consented = True
    await db_session.commit()

    session, _ = await create_counseling_request(db_session, student)

    await broadcast_new_request_to_counselors(mock_bot, [counselor], session.id)

    assert mock_bot.send_message.call_count == 1
    call_args = mock_bot.send_message.call_args[1]
    broadcast_text = call_args["text"]

    # Session ID must be present
    assert f"#{session.id}" in broadcast_text
    # Student telegram_id must NEVER be present
    assert str(student.telegram_id) not in broadcast_text


@pytest.mark.asyncio
async def test_zero_message_persistence_in_models():
    # Verify no message content columns exist in the database models
    session_columns = [col.name for col in CounselingSession.__table__.columns]
    assert "message" not in session_columns
    assert "body" not in session_columns
    assert "content" not in session_columns
    assert "transcript" not in session_columns

    user_columns = [col.name for col in User.__table__.columns]
    assert "message" not in user_columns
    assert "username" not in user_columns


# ============================================================================
# 3. Counselor Matching and Atomic Concurrency Tests
# ============================================================================


@pytest.mark.asyncio
async def test_atomic_counselor_acceptance(db_session, test_settings):
    student = await get_or_create_user(db_session, 3001, test_settings.LEADER_IDS)
    student.consented = True

    counselor1 = await get_or_create_user(db_session, 3002, test_settings.LEADER_IDS)
    counselor1.is_approved_counselor = True
    counselor1.consented = True

    counselor2 = await get_or_create_user(db_session, 3003, test_settings.LEADER_IDS)
    counselor2.is_approved_counselor = True
    counselor2.consented = True

    await db_session.commit()

    session, _ = await create_counseling_request(db_session, student)
    assert session.status == SessionStatus.WAITING

    # Counselor 1 accepts first -> Succeeds
    success1, _, claimed1 = await accept_session_atomically(db_session, session.id, counselor1)
    assert success1 is True
    assert claimed1 is not None
    assert claimed1.counselor_id == counselor1.id
    assert claimed1.status == SessionStatus.ACTIVE

    # Counselor 2 attempts to accept the SAME session -> Fails
    success2, reason2, claimed2 = await accept_session_atomically(db_session, session.id, counselor2)
    assert success2 is False
    assert claimed2 is None
    assert "ቀድሞውኑ በሌላ አማካሪ ተወስዷል" in reason2


@pytest.mark.asyncio
async def test_counselor_cannot_accept_multiple_concurrent_sessions(db_session, test_settings):
    student1 = await get_or_create_user(db_session, 3101, test_settings.LEADER_IDS)
    student1.consented = True
    student2 = await get_or_create_user(db_session, 3102, test_settings.LEADER_IDS)
    student2.consented = True

    counselor = await get_or_create_user(db_session, 3103, test_settings.LEADER_IDS)
    counselor.is_approved_counselor = True
    counselor.consented = True
    await db_session.commit()

    sess1, _ = await create_counseling_request(db_session, student1)
    sess2, _ = await create_counseling_request(db_session, student2)

    # First acceptance succeeds
    ok1, _, _ = await accept_session_atomically(db_session, sess1.id, counselor)
    assert ok1 is True

    # Second acceptance while active fails
    ok2, reason, _ = await accept_session_atomically(db_session, sess2.id, counselor)
    assert ok2 is False
    assert "ቀድሞውኑ በሂደት ላይ ያለ የምክር ውይይት አለዎት" in reason


@pytest.mark.asyncio
async def test_student_cannot_accept_own_session(db_session, test_settings):
    user = await get_or_create_user(db_session, 3201, test_settings.LEADER_IDS)
    user.is_approved_counselor = True
    user.consented = True
    await db_session.commit()

    sess, _ = await create_counseling_request(db_session, user)
    ok, _, _ = await accept_session_atomically(db_session, sess.id, user)
    assert ok is False


# ============================================================================
# 4. Message Routing and Session Ownership Tests
# ============================================================================


@pytest.mark.asyncio
async def test_message_relay_routing(db_session, test_settings, mock_bot):
    student = await get_or_create_user(db_session, 4001, test_settings.LEADER_IDS)
    student.consented = True
    counselor = await get_or_create_user(db_session, 4002, test_settings.LEADER_IDS)
    counselor.is_approved_counselor = True
    counselor.consented = True
    await db_session.commit()

    session, _ = await create_counseling_request(db_session, student)
    await accept_session_atomically(db_session, session.id, counselor)
    await db_session.refresh(session)

    # 1. Student sends message -> delivered to Counselor
    success, err = await relay_session_message(
        bot=mock_bot,
        db=db_session,
        settings=test_settings,
        sender=student,
        session=session,
        text="ሰላም አማካሪ፣ ጸሎት እፈልጋለሁ።",
    )
    assert success is True
    assert err is None
    counselor_call = mock_bot.send_message.call_args[1]
    assert counselor_call["chat_id"] == counselor.telegram_id
    assert f"ተማሪ #{session.id}" in counselor_call["text"]
    assert str(student.telegram_id) not in counselor_call["text"]

    # 2. Counselor replies -> delivered to Student
    mock_bot.send_message.reset_mock()
    success, err = await relay_session_message(
        bot=mock_bot,
        db=db_session,
        settings=test_settings,
        sender=counselor,
        session=session,
        text="ሰላም ወንድሜ፣ ጌታ ይባርክህ። ምን እንጸልይ?",
    )
    assert success is True
    assert err is None
    student_call = mock_bot.send_message.call_args[1]
    assert student_call["chat_id"] == student.telegram_id
    assert "አማካሪ" in student_call["text"]


@pytest.mark.asyncio
async def test_unauthorized_party_cannot_relay_message(db_session, test_settings, mock_bot):
    student = await get_or_create_user(db_session, 4101, test_settings.LEADER_IDS)
    counselor = await get_or_create_user(db_session, 4102, test_settings.LEADER_IDS)
    counselor.is_approved_counselor = True
    counselor.consented = True
    outsider = await get_or_create_user(db_session, 4103, test_settings.LEADER_IDS)
    await db_session.commit()

    session, _ = await create_counseling_request(db_session, student)
    await accept_session_atomically(db_session, session.id, counselor)

    success, err = await relay_session_message(
        bot=mock_bot,
        db=db_session,
        settings=test_settings,
        sender=outsider,
        session=session,
        text="ያልተፈቀደ ጣልቃ ገብነት",
    )
    assert success is False
    assert "የዚህ ውይይት ተሳታፊ አይደሉም" in err


# ============================================================================
# 5. Unauthorized Commands Tests
# ============================================================================


@pytest.mark.asyncio
async def test_unauthorized_leader_commands(db_session, test_settings):
    # Non-leader user
    regular_user = await get_or_create_user(db_session, 5001, test_settings.LEADER_IDS)
    assert regular_user.role == UserRole.STUDENT

    mock_update = MagicMock()
    mock_update.effective_user.id = regular_user.telegram_id
    mock_update.message.reply_text = AsyncMock()
    mock_context = MagicMock()
    mock_context.args = ["5002"]

    # Try add_counselor
    await add_counselor_command(mock_update, mock_context)
    call_msg = mock_update.message.reply_text.call_args[0][0]
    assert "ለAMUECSF አመራሮች" in call_msg

    # Try remove_counselor
    mock_update.message.reply_text.reset_mock()
    await remove_counselor_command(mock_update, mock_context)
    call_msg = mock_update.message.reply_text.call_args[0][0]
    assert "ለAMUECSF አመራሮች" in call_msg

    # Try list_counselors
    mock_update.message.reply_text.reset_mock()
    await list_counselors_command(mock_update, mock_context)
    call_msg = mock_update.message.reply_text.call_args[0][0]
    assert "ለAMUECSF አመራሮች" in call_msg


@pytest.mark.asyncio
async def test_authorized_leader_can_manage_counselors(db_session, test_settings):
    # Leader is in test_settings.LEADER_IDS [999001, 999002]
    leader = await get_or_create_user(db_session, 999001, test_settings.LEADER_IDS)
    assert leader.role == UserRole.LEADER

    mock_update = MagicMock()
    mock_update.effective_user.id = leader.telegram_id
    mock_update.message.reply_text = AsyncMock()
    mock_context = MagicMock()
    mock_context.args = ["555555"]

    # 1. Add counselor
    await add_counselor_command(mock_update, mock_context)
    success_msg = mock_update.message.reply_text.call_args[0][0]
    assert "እንደ አማካሪ (Counselor) ተመዝግቧል" in success_msg

    target = await get_or_create_user(db_session, 555555, test_settings.LEADER_IDS)
    assert target.is_approved_counselor is True
    assert target.role == UserRole.COUNSELOR

    # 2. Remove counselor
    mock_update.message.reply_text.reset_mock()
    await remove_counselor_command(mock_update, mock_context)
    remove_msg = mock_update.message.reply_text.call_args[0][0]
    assert "ከአማካሪነት ተነስቷል" in remove_msg

    await db_session.refresh(target)
    assert target.is_approved_counselor is False


# ============================================================================
# 6. Crisis Detection and Alerts Tests
# ============================================================================


def test_crisis_evaluation_patterns():
    # Amharic triggers
    assert evaluate_message_safety("ራሴን ማጥፋት እፈልጋለሁ").is_crisis is True
    assert evaluate_message_safety("መሞት ይሻለኛል").is_crisis is True
    assert evaluate_message_safety("ህይወቴን ማቆም አስባለሁ").is_crisis is True
    assert evaluate_message_safety("በህይወት መኖር ሰለቸኝ").is_crisis is True

    # English triggers
    assert evaluate_message_safety("I want to kill myself").is_crisis is True
    assert evaluate_message_safety("having suicidal thoughts").is_crisis is True
    assert evaluate_message_safety("thinking of self-harm").is_crisis is True

    # Safe messages
    assert evaluate_message_safety("ሰላም እንዴት ነህ?").is_crisis is False
    assert evaluate_message_safety("ስለ ትምህርት ምክር እፈልጋለሁ").is_crisis is False
    assert evaluate_message_safety("Can we pray for my exams?").is_crisis is False

    # Fail closed on malformed input
    eval_none = evaluate_message_safety(None)
    assert eval_none.is_crisis is True
    assert eval_none.fail_closed_error is True


@pytest.mark.asyncio
async def test_crisis_triggers_alert_and_emergency_contacts(db_session, test_settings, mock_bot):
    student = await get_or_create_user(db_session, 6001, test_settings.LEADER_IDS)
    student.consented = True
    counselor = await get_or_create_user(db_session, 6002, test_settings.LEADER_IDS)
    counselor.is_approved_counselor = True
    counselor.consented = True
    await db_session.commit()

    session, _ = await create_counseling_request(db_session, student)
    await accept_session_atomically(db_session, session.id, counselor)
    await db_session.refresh(session)

    # Student sends crisis message
    success, err = await relay_session_message(
        bot=mock_bot,
        db=db_session,
        settings=test_settings,
        sender=student,
        session=session,
        text="ህይወቴን ማጥፋት እፈልጋለሁ",
    )

    await db_session.refresh(session)
    # Session must be flagged
    assert session.crisis_flagged is True

    # Verify emergency message sent to student
    sent_chats = [call[1]["chat_id"] for call in mock_bot.send_message.call_args_list]
    # Leader / crisis alert chat notified
    assert test_settings.CRISIS_ALERT_CHAT_ID in sent_chats
    # Student notified with emergency numbers
    assert student.telegram_id in sent_chats


# ============================================================================
# 7. Session Cleanup Tests
# ============================================================================


@pytest.mark.asyncio
async def test_session_cleanup(db_session, test_settings, mock_bot):
    student = await get_or_create_user(db_session, 7001, test_settings.LEADER_IDS)
    student.consented = True
    counselor = await get_or_create_user(db_session, 7002, test_settings.LEADER_IDS)
    counselor.is_approved_counselor = True
    counselor.consented = True
    await db_session.commit()

    session, _ = await create_counseling_request(db_session, student)
    await accept_session_atomically(db_session, session.id, counselor)
    await db_session.refresh(session)

    assert session.status == SessionStatus.ACTIVE

    # Student ends session
    closed = await close_session_for_user(db_session, student)
    assert closed is not None
    assert closed.status == SessionStatus.CLOSED
    assert closed.closed_at is not None

    # Verify active session is cleared
    active_now = await get_active_session_for_user(db_session, student)
    assert active_now is None

    # Test notification dispatch
    await notify_session_closed(mock_bot, db_session, closed, closed_by_user_id=student.id)
    assert mock_bot.send_message.call_count == 2
