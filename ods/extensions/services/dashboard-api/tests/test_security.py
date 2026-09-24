"""Tests for security.py — API key authentication."""

import os
import stat

import pytest

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import security


def test_generated_key_is_reused_after_restart(tmp_path, monkeypatch):
    key_file = tmp_path / "dashboard-api-key.txt"
    generated = security._load_or_create_api_key(None, key_file)

    monkeypatch.setattr(
        security.secrets,
        "token_urlsafe",
        lambda _size: pytest.fail("persisted key should be reused"),
    )

    assert security._load_or_create_api_key(None, key_file) == generated


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not enforced on Windows")
def test_generated_key_file_is_private(tmp_path):
    key_file = tmp_path / "dashboard-api-key.txt"

    security._load_or_create_api_key(None, key_file)

    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


def test_explicit_key_does_not_touch_persisted_file(tmp_path):
    key_file = tmp_path / "dashboard-api-key.txt"

    assert security._load_or_create_api_key("configured", key_file) == "configured"
    assert not key_file.exists()


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
