"""A failed Portal turn names its cause from the relay's latest model call."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import pixel_model_failure as pmf

HEADERS = {"Authorization": "Bearer test-key-12345"}


@pytest.mark.parametrize(("status", "kind", "cause", "text"), [
    (401, "api", "key_refused", "Rerun the ODS installer with a current API key."),
    (401, "remote", "key_refused", "Connect it again with a current key in Settings > Remote model."),
    (403, "remote", "access_denied", "Check that your key may use this model"),
    (404, "api", "model_missing", "Check the model name."),
    (429, "remote", "rate_limited", "Wait a minute, then try again."),
    (500, "remote", "api_unreachable", "ODS could not reach the model API, or the API failed (HTTP 500)."),
    (502, "api", "api_unreachable", "(HTTP 502)"),
    (503, "local", "model_loading", "It may still be loading"),
    (502, "local", "model_unavailable", "The local model did not answer (HTTP 502)."),
])
def test_failure_cause_names_the_next_step(status, kind, cause, text):
    value = pmf.failure_cause(status, kind)
    assert value["cause"] == cause
    assert text in value["message"]


@pytest.mark.parametrize("status", [401, 403, 404, 429])
def test_a_local_install_names_no_api_cause(status):
    assert pmf.failure_cause(status, "local") is None


@pytest.fixture
def client(monkeypatch):
    env = {"PIXEL_MODEL_RELAY_KEY": "relay-key", "LLM_BACKEND": "external", "ODS_MODE": "local"}
    monkeypatch.setattr(pmf, "read_live_env_value", lambda key, default="": env.get(key, default))
    monkeypatch.setattr(pmf, "SERVICES", {"pixel-model-relay": {"host": "pixel-model-relay", "port": 4102}})
    app = FastAPI()
    app.include_router(pmf.router)
    with TestClient(app) as value:
        value.env = env
        yield value


def relay_answers(monkeypatch, *, status_code=200, body=None, error=None):
    seen: list[tuple[str, dict]] = []

    class Client:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, url, headers):
            seen.append((url, headers))
            if error:
                raise error
            return SimpleNamespace(status_code=status_code, json=lambda: body)

    monkeypatch.setattr(pmf.httpx, "AsyncClient", Client)
    return seen


def test_requires_owner_auth(client, monkeypatch):
    seen = relay_answers(monkeypatch, body={"status": 401, "ageSeconds": 3})
    assert client.get("/api/pixel/model-failure").status_code == 401
    assert seen == []


def test_a_recent_failure_names_its_cause(client, monkeypatch):
    # Fleet drill: a refused key looked like every other failure in Portal.
    seen = relay_answers(monkeypatch, body={"status": 401, "ageSeconds": 3.5})
    result = client.get("/api/pixel/model-failure", headers=HEADERS)
    assert result.status_code == 200
    assert result.json() == {
        "cause": "key_refused", "status": 401,
        "message": "The model API refused the key ODS uses (HTTP 401). Rerun the ODS installer with a current API key."}
    assert seen == [("http://pixel-model-relay:4102/v1/ods/last-generation", {"Authorization": "Bearer relay-key"})]


@pytest.mark.parametrize("body", [
    {"status": 200, "ageSeconds": 2},
    {"status": 401, "ageSeconds": 600},
    {"status": None, "ageSeconds": None},
    {"status": "401", "ageSeconds": 2},
    ["not", "an", "object"],
])
def test_no_recent_failure_names_no_cause(client, monkeypatch, body):
    relay_answers(monkeypatch, body=body)
    assert client.get("/api/pixel/model-failure", headers=HEADERS).json() == {"cause": None}


def test_an_unreachable_relay_names_no_cause(client, monkeypatch):
    relay_answers(monkeypatch, error=httpx.ConnectError("refused"))
    assert client.get("/api/pixel/model-failure", headers=HEADERS).json() == {"cause": None}


def test_without_the_relay_key_nothing_is_asked(client, monkeypatch):
    client.env["PIXEL_MODEL_RELAY_KEY"] = ""
    seen = relay_answers(monkeypatch, body={"status": 401, "ageSeconds": 3})
    assert client.get("/api/pixel/model-failure", headers=HEADERS).json() == {"cause": None}
    assert seen == []
