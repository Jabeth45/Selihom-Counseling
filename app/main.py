import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Request, status
from telegram import Update
from app.bot import create_telegram_app
from app.config import get_settings
from app.database import init_db

logger = logging.getLogger(__name__)
settings = get_settings()
telegram_app = create_telegram_app(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    logger.info("Initializing database schema...")
    await init_db()

    bot_started = False
    try:
        await telegram_app.initialize()
        await telegram_app.start()
        bot_started = True

        if settings.BOT_MODE == "polling":
            logger.info("Starting Telegram bot in POLLING mode...")
            if telegram_app.updater is not None:
                await telegram_app.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram bot polling started successfully.")
        elif settings.BOT_MODE == "webhook":
            logger.info("Configuring Telegram bot in WEBHOOK mode...")
            if settings.WEBHOOK_URL:
                webhook_url = f"{settings.WEBHOOK_URL.rstrip('/')}/telegram-webhook"
                await telegram_app.bot.set_webhook(
                    url=webhook_url,
                    secret_token=settings.WEBHOOK_SECRET_TOKEN or None,
                    drop_pending_updates=True,
                )
                logger.info("Webhook registered at: %s", webhook_url)
            else:
                logger.warning("WEBHOOK_URL not set; webhook registration skipped.")
    except Exception as exc:
        logger.error(
            "Telegram bot failed to start: %s. Ensure TELEGRAM_BOT_TOKEN is valid.",
            exc,
        )

    yield

    if bot_started:
        logger.info("Shutting down Telegram bot...")
        try:
            if settings.BOT_MODE == "polling":
                if telegram_app.updater and telegram_app.updater.running:
                    await telegram_app.updater.stop()
            elif settings.BOT_MODE == "webhook":
                try:
                    await telegram_app.bot.delete_webhook()
                except Exception:
                    pass
            await telegram_app.stop()
            await telegram_app.shutdown()
        except Exception as exc:
            logger.warning("Error during bot shutdown: %s", exc)
        logger.info("Telegram bot shutdown completed.")


app = FastAPI(
    title="AMUECSF Anonymous Counseling Bot",
    description="Production-conscious anonymous counseling Telegram bot for AMUECSF",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/", tags=["Health"])
async def root():
    return {
        "service": "AMUECSF Anonymous Counseling Bot",
        "title_am": "የAMUECSF ሚስጥራዊ የምክር አገልግሎት",
        "status": "online",
        "mode": settings.BOT_MODE,
        "environment": settings.ENVIRONMENT,
    }


@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "ok",
        "mode": settings.BOT_MODE,
        "environment": settings.ENVIRONMENT,
    }


@app.post("/telegram-webhook", tags=["Webhook"])
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if settings.WEBHOOK_SECRET_TOKEN:
        if x_telegram_bot_api_secret_token != settings.WEBHOOK_SECRET_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid webhook secret token",
            )

    try:
        data = await request.json()
        update = Update.de_json(data=data, bot=telegram_app.bot)
        if update:
            await telegram_app.process_update(update)
        return {"ok": True}
    except Exception as exc:
        logger.error("Error processing telegram update: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error processing update: {str(exc)}",
        )
