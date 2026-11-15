from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


def consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="✅ እስማማለሁ (I Agree & Consent)",
                    callback_data="consent:accept",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ አልስማማም (Decline)",
                    callback_data="consent:decline",
                )
            ],
        ]
    )


def student_main_keyboard(has_active_session: bool = False) -> ReplyKeyboardMarkup:
    if has_active_session:
        buttons = [
            ["🛑 ውይይቱን ጨርስ (End Session)"],
            ["🆘 የአደጋ ጊዜ እርዳታ (Emergency Help)"],
        ]
    else:
        buttons = [
            ["🤝 የምክር አገልግሎት ጠይቅ (Request Counseling)"],
            ["🆘 የአደጋ ጊዜ እርዳታ (Emergency Help)", "ℹ️ ስለ አገልግሎቱ (About)"],
        ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def counselor_main_keyboard(is_available: bool, has_active_session: bool = False) -> ReplyKeyboardMarkup:
    if has_active_session:
        buttons = [
            ["🛑 ውይይቱን ጨርስ (End Session)"],
            ["🆘 የአደጋ ጊዜ መመሪያ (Crisis Protocol)"],
        ]
    else:
        toggle_label = (
            "🔴 ከመስመር ውጣ (Go Offline)"
            if is_available
            else "🟢 ዝግጁ ነኝ (Go Available)"
        )
        buttons = [
            [toggle_label],
            ["📋 በመጠባበቅ ላይ ያሉ ጥያቄዎች (Waiting Requests)"],
        ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def accept_session_keyboard(session_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text=f"✅ ጥያቄ #{session_id} ተቀበል (Accept Session)",
                    callback_data=f"session:accept:{session_id}",
                )
            ]
        ]
    )
