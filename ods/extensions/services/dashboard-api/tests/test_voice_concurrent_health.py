import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from routers.voice import router
from security import verify_api_key

app = FastAPI()
app.include_router(router)
app.dependency_overrides[verify_api_key] = lambda: "test-api-key"

client = TestClient(app)

def test_voice_status_concurrent():
    with patch("helpers.check_service_health", new_callable=AsyncMock) as mock_health:
        from models import ServiceStatus
        mock_health.return_value = ServiceStatus(
            id="test", name="test", port=80, external_port=80, status="healthy"
        )
        resp = client.get("/api/voice/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "services" in data
        assert "stt" in data["services"]
        assert "tts" in data["services"]
