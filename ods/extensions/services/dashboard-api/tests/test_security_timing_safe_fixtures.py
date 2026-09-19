import pytest
from pathlib import Path
import sys
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

import security

@pytest.mark.asyncio
async def test_valid_api_key(monkeypatch):
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "correct-secret-key")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="correct-secret-key")
    result = await security.verify_api_key(creds)
    assert result == "correct-secret-key"

@pytest.mark.asyncio
async def test_invalid_api_key(monkeypatch):
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "correct-secret-key")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong-secret-key")
    with pytest.raises(HTTPException) as exc_info:
        await security.verify_api_key(creds)
    assert exc_info.value.status_code == 403

@pytest.mark.asyncio
async def test_missing_credentials_unauthorized():
    with pytest.raises(HTTPException) as exc_info:
        await security.verify_api_key(None)
    assert exc_info.value.status_code == 401

@pytest.mark.asyncio
async def test_empty_credentials_rejected(monkeypatch):
    monkeypatch.setattr(security, "DASHBOARD_API_KEY", "correct-secret-key")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="")
    with pytest.raises(HTTPException) as exc_info:
        await security.verify_api_key(creds)
    assert exc_info.value.status_code == 403
