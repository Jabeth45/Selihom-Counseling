from unittest.mock import AsyncMock, MagicMock
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from app.config import Settings
from app.database import Base
import app.database
import app.handlers


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        BOT_MODE="polling",
        ENVIRONMENT="development",
        TELEGRAM_BOT_TOKEN="123456:mock_token_for_testing",
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        LEADER_IDS=[999001, 999002],
        CRISIS_ALERT_CHAT_ID=888001,
    )


@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine, monkeypatch, test_settings):
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    monkeypatch.setattr(app.database, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(app.handlers, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(app.handlers, "get_settings", lambda: test_settings)

    async with session_factory() as session:
        yield session


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock())
    return bot
