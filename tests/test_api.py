from unittest.mock import AsyncMock, MagicMock
import pytest
from httpx import ASGITransport, AsyncClient
import app.main
from app.config import get_settings


@pytest.mark.asyncio
async def test_root_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app.main.app), base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "AMUECSF Anonymous Counseling Bot"
        assert data["status"] == "online"


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app.main.app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_webhook_secret_verification(monkeypatch):
    settings = get_settings()
    settings.WEBHOOK_SECRET_TOKEN = "super_secret_test_token"

    mock_app = MagicMock()
    mock_app.bot = MagicMock()
    mock_app.process_update = AsyncMock()
    monkeypatch.setattr(app.main, "telegram_app", mock_app)

    async with AsyncClient(transport=ASGITransport(app=app.main.app), base_url="http://test") as client:
        # 1. Invalid secret token -> 403
        bad_response = await client.post(
            "/telegram-webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_token"},
            json={"update_id": 12345},
        )
        assert bad_response.status_code == 403

        # 2. Valid secret token -> 200
        good_response = await client.post(
            "/telegram-webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "super_secret_test_token"},
            json={"update_id": 12345},
        )
        assert good_response.status_code == 200
        assert good_response.json() == {"ok": True}
        assert mock_app.process_update.awaited
