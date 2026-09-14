"""Unit tests for security API key verification resilience."""

from unittest.mock import MagicMock
import pytest
from fastapi import HTTPException

from security import verify_api_key, DASHBOARD_API_KEY


@pytest.mark.asyncio
async def test_verify_api_key_missing_credentials_raises_401():
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_api_key_invalid_credentials_raises_403():
    creds = MagicMock()
    creds.credentials = "invalid-token-12345"
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(creds)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_verify_api_key_valid_credentials_returns_token():
    creds = MagicMock()
    creds.credentials = DASHBOARD_API_KEY
    token = await verify_api_key(creds)
    assert token == DASHBOARD_API_KEY
