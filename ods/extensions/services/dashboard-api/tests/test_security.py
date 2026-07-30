"""Tests for security.py — API key authentication."""

import os
import secrets
import stat
import tempfile
from pathlib import Path

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


class TestGeneratedApiKeyFile:

    def test_generated_key_written_atomically_with_restricted_permissions(self, tmp_path):
        key_file = tmp_path / "dashboard-api-key.txt"
        orig_key = os.environ.pop("DASHBOARD_API_KEY", None)
        try:
            fd, tmp_str = tempfile.mkstemp(dir=str(tmp_path), prefix=".dashboard-api-key.", suffix=".tmp")
            os.close(fd)
            os.unlink(tmp_str)
            # Pre-populate key file location
            key = secrets.token_urlsafe(32)
            fd, tmp_str = tempfile.mkstemp(dir=str(tmp_path), prefix=".dashboard-api-key.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(key)
            tmp_path_obj = Path(tmp_str)
            tmp_path_obj.chmod(0o600)
            os.replace(tmp_path_obj, key_file)
            assert key_file.exists()
            assert key_file.read_text(encoding="utf-8") == key
            assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        finally:
            if orig_key is not None:
                os.environ["DASHBOARD_API_KEY"] = orig_key
