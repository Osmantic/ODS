"""Tests for BARK_API_KEY enforcement.

The key was passed into the container by compose and ignored by the server,
leaving /tts, /tts/stream and /voices open to anyone who could reach the port.
"""

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

# bark is a multi-GB ML dependency that is not installed in CI; server.py only
# imports it lazily inside the generation helpers.
sys.modules.setdefault("bark", MagicMock(SAMPLE_RATE=24000))

import server  # noqa: E402

KEY = "s3cret-key"
GATED_ROUTES = ("/tts", "/tts/stream", "/voices")


def build(api_key):
    """Re-import server with BARK_API_KEY set, as the container would at boot."""
    with patch.dict("os.environ", {"BARK_API_KEY": api_key}):
        module = importlib.reload(server)
    return module, TestClient(module.app)


def call(client, route, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if route == "/voices":
        return client.get(route, headers=headers)
    return client.post(route, json={"text": "hi"}, headers=headers)


@pytest.fixture(autouse=True)
def restore():
    yield
    build("")


# --- a configured key is enforced -------------------------------------------

@pytest.mark.parametrize("route", GATED_ROUTES)
def test_configured_key_rejects_missing_token(route):
    _, client = build(KEY)

    assert call(client, route).status_code == 401


@pytest.mark.parametrize("route", GATED_ROUTES)
def test_configured_key_rejects_wrong_token(route):
    _, client = build(KEY)

    response = call(client, route, token="wrong-key")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API key"


def test_configured_key_accepts_correct_token():
    _, client = build(KEY)

    assert call(client, "/voices", token=KEY).status_code == 200


def test_a_token_that_is_a_prefix_of_the_key_is_rejected():
    _, client = build(KEY)

    assert call(client, "/voices", token=KEY[:-1]).status_code == 401


def test_non_ascii_token_is_rejected_not_a_server_error():
    """compare_digest raises TypeError on non-ASCII str, which would be a 500.

    Calls the dependency directly: HTTP headers are ASCII-only, so the client
    rejects such a token before it can reach the server.
    """
    module, _ = build(KEY)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="ключ")

    with pytest.raises(HTTPException) as exc:
        module.verify_api_key(credentials)

    assert exc.value.status_code == 401


# --- health stays reachable for the container healthcheck -------------------

def test_health_needs_no_token_even_when_a_key_is_set():
    _, client = build(KEY)

    assert client.get("/health").status_code == 200


# --- unset key preserves today's open behaviour -----------------------------

@pytest.mark.parametrize("route", GATED_ROUTES)
def test_unset_key_leaves_routes_open(route):
    _, client = build("")

    assert call(client, route).status_code != 401


def test_unset_key_warns_at_startup(caplog):
    with caplog.at_level("WARNING"):
        build("")

    assert any("BARK_API_KEY is not set" in r.message for r in caplog.records)


def test_a_configured_key_does_not_warn(caplog):
    with caplog.at_level("WARNING"):
        build(KEY)

    assert not any("BARK_API_KEY is not set" in r.message for r in caplog.records)
