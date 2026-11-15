from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str) -> AsyncEngine:
    connect_args = {}
    clean_url = database_url

    # asyncpg handles SSL via connect_args rather than libpq sslmode query params
    if "sslmode=require" in clean_url:
        clean_url = clean_url.replace("?sslmode=require", "").replace("&sslmode=require", "")
        # If removing ? left an & as first query param, fix it
        if "?" not in clean_url and "&" in clean_url:
            clean_url = clean_url.replace("&", "?", 1)
        connect_args["ssl"] = "require"

    is_sqlite = clean_url.startswith("sqlite")
    engine_kwargs = {
        "echo": False,
        "connect_args": connect_args,
    }
    if not is_sqlite:
        engine_kwargs.update(
            {
                "pool_pre_ping": True,
                "pool_size": 5,
                "max_overflow": 10,
                "pool_recycle": 300,
            }
        )

    return create_async_engine(clean_url, **engine_kwargs)


settings = get_settings()
engine = build_engine(settings.DATABASE_URL)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db(target_engine: AsyncEngine = engine) -> None:
    from app import models  # noqa: F401

    async with target_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
