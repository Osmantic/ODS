"""Tests for security.py — API key authentication."""

import pytest

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import security


class TestVerifyApiKey:

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        """Pin the API key to a known value for testing."""
        monkeypatch.setattr(security, "DASHBOARD_API_KEY", "test-secret-key-12345")

    @pytest.mark.asyncio
    async def test_valid_key_returns_key(self):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-secret-key-12345")
        result = await security.verify_api_key(creds)
        assert result == "test-secret-key-12345"

    @pytest.mark.asyncio
    async def test_invalid_key_raises_403(self):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong-key")
        with pytest.raises(HTTPException) as exc_info:
            await security.verify_api_key(creds)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_credentials_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            await security.verify_api_key(None)
        assert exc_info.value.status_code == 401
