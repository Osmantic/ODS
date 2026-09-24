"""Tests for security.py — API key authentication."""

import os

import pytest

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import security


def test_fallback_key_survives_process_restart(tmp_path):
    key_file = tmp_path / "dashboard-api-key.txt"

    first_key, first_created = security._load_or_create_api_key(key_file)
    second_key, second_created = security._load_or_create_api_key(key_file)

    assert first_created is True
    assert second_created is False
    assert second_key == first_key
    if os.name != "nt":
        assert key_file.stat().st_mode & 0o777 == 0o600


def test_empty_fallback_key_fails_closed(tmp_path):
    key_file = tmp_path / "dashboard-api-key.txt"
    key_file.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="key file is empty"):
        security._load_or_create_api_key(key_file)


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

    @pytest.mark.asyncio
    @pytest.mark.parametrize("token", ["\xe9", "kéy", "\U0001F600"])
    async def test_non_ascii_key_raises_403_not_typeerror(self, token):
        """Authorization headers decode as latin-1, so an unauthenticated
        client can present a non-ASCII token. It must be a plain mismatch
        rather than a TypeError surfacing as a 500."""
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with pytest.raises(HTTPException) as exc_info:
            await security.verify_api_key(creds)
        assert exc_info.value.status_code == 403
