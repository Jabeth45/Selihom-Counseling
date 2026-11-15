from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from app.config import Settings
from app.handlers import (
    about_action,
    add_counselor_command,
    emergency_help_action,
    handle_consent_callback,
    handle_session_accept_callback,
    handle_text_message,
    list_counselors_command,
    remove_counselor_command,
    start_command,
)


def create_telegram_app(settings: Settings) -> Application:
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("about", about_action))
    app.add_handler(CommandHandler("help", about_action))
    app.add_handler(CommandHandler("emergency", emergency_help_action))
    app.add_handler(CommandHandler("add_counselor", add_counselor_command))
    app.add_handler(CommandHandler("remove_counselor", remove_counselor_command))
    app.add_handler(CommandHandler("list_counselors", list_counselors_command))

    # Inline Keyboard Callback Handlers
    app.add_handler(CallbackQueryHandler(handle_consent_callback, pattern=r"^consent:"))
    app.add_handler(CallbackQueryHandler(handle_session_accept_callback, pattern=r"^session:accept:"))

    # Incoming Text Router
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    return app
