"""dashboard-api proxies for model profiles (PLAN WP3.5)."""

from __future__ import annotations

import pytest

import routers.models as models_router
from host_agent_client import AgentHTTPError


def test_profile_route_asks_the_agent_for_that_model(test_client, monkeypatch):
    seen = []

    def agent(method, path, *, params=None, timeout):
        seen.append((method, path, params))
        return {"mode": "observe", "modelId": "qwen3.5-9b", "profile": None}

    monkeypatch.setattr(models_router, "request_agent_json", agent)

    response = test_client.get("/api/models/qwen3.5-9b/profile", headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json() == {"mode": "observe", "modelId": "qwen3.5-9b", "profile": None}
    assert seen == [("GET", "/v1/model/profile", {"model": "qwen3.5-9b"})]


def test_profile_route_requires_the_api_key(test_client):
    assert test_client.get("/api/models/qwen3.5-9b/profile").status_code in {401, 403}


def test_recheck_route_forwards_to_the_agent_with_room_for_the_budget(test_client, monkeypatch):
    seen = []

    def agent(path, body, timeout=30, **_kwargs):
        seen.append((path, body, timeout))
        return {"status": "recorded", "keyHash": "f" * 64, "summary": {"chat": True}}

    monkeypatch.setattr(models_router, "_call_agent_model", agent)

    response = test_client.post("/api/models/qwen3.5-9b/profile/recheck", headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "recorded"
    assert seen == [("/v1/model/profile/recheck", {"model": "qwen3.5-9b"}, 180)]


def test_recheck_of_a_model_that_is_not_running_is_a_409(test_client, monkeypatch):
    def refuse(method, path, *, payload=None, timeout):
        raise AgentHTTPError(409, "conflict", response_text=(
            '{"error": "Only the running model can be checked; run it first", "code": "not_running"}'))

    monkeypatch.setattr(models_router, "request_agent_json", refuse)

    response = test_client.post("/api/models/other/profile/recheck", headers=test_client.auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "not_running"


@pytest.mark.parametrize("operation, words", [
    ("model_profile_recheck", "checking what the running model can do"),
])
def test_a_download_during_a_recheck_is_refused_in_words(operation, words):
    detail = models_router._lifecycle_busy_detail({"code": "model_lifecycle_busy", "activeOperation": operation})
    assert words in detail["message"]
