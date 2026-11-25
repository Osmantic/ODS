"""A definitive busy refusal may be retried only within the remaining grace."""
import json
from typing import Any

import httpx
import pytest


def _busy(router):
    return router.AgentHTTPError(409, "busy", json.dumps({
        "code": "model_lifecycle_busy", "activeOperation": "artifact_verification",
    }))


@pytest.fixture
def clock(monkeypatch):
    import routers.models as router

    state: dict[str, Any] = {"now": 0.0, "sleeps": []}

    def sleep(seconds):
        state["sleeps"].append(seconds)
        state["now"] += seconds

    monkeypatch.setattr(router.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(router.time, "sleep", sleep)
    return state


def test_busy_response_near_deadline_never_starts_another_request(monkeypatch, clock):
    import routers.models as router

    calls: list[float] = []

    def request(*_args, timeout, **_kwargs):
        calls.append(timeout)
        clock["now"] += 29.8
        raise _busy(router)

    monkeypatch.setattr(router, "request_agent_json", request)
    with pytest.raises(router.HTTPException) as raised:
        router._request_agent_download({})
    assert raised.value.status_code == 409
    assert isinstance(raised.value.detail, dict)
    assert raised.value.detail["code"] == "model_lifecycle_busy"
    assert calls == [30.0]
    assert clock["now"] == pytest.approx(30.0)
    assert clock["sleeps"] == pytest.approx([0.2])


def test_retry_uses_remaining_float_timeout_through_real_client(monkeypatch, clock):
    import host_agent_client
    import routers.models as router

    calls: list[tuple[str, str, float]] = []

    class Client:
        def request(self, method, path, **kwargs):
            timeout = kwargs["timeout"]
            calls.append((method, path, timeout.read))
            clock["now"] += min(20.0, timeout.read)
            return httpx.Response(409, json={"code": "model_lifecycle_busy",
                "activeOperation": "artifact_verification"})

    monkeypatch.setattr(host_agent_client, "_get_sync_client", lambda: Client())
    with pytest.raises(router.HTTPException) as raised:
        router._request_agent_download({})
    assert raised.value.status_code == 409
    assert calls == [("POST", "/v1/model/download", 30.0),
                     ("POST", "/v1/model/download", 9.5)]
    assert clock["now"] == 30.0


def test_success_within_remaining_grace_is_returned_once(monkeypatch, clock):
    import routers.models as router

    timeouts: list[float] = []

    def request(*_args, timeout, **_kwargs):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            clock["now"] += 20.0
            raise _busy(router)
        clock["now"] += 9.0
        return {"status": "started"}

    monkeypatch.setattr(router, "request_agent_json", request)
    assert router._request_agent_download({}) == {"status": "started"}
    assert timeouts == [30.0, 9.5]
    assert clock["now"] == 29.5


@pytest.mark.parametrize("failure", ["timeout", "protocol", "uncoded_409", "long_busy"])
def test_only_definitive_short_busy_refusals_are_retried(monkeypatch, clock, failure):
    import routers.models as router

    calls: list[bool] = []

    def request(*_args, **_kwargs):
        calls.append(True)
        if failure == "timeout":
            raise router.AgentUnavailable("synthetic timeout")
        if failure == "protocol":
            raise router.AgentProtocolError("synthetic response error")
        if failure == "uncoded_409":
            raise router.AgentHTTPError(409, "unconfirmed", '{"error":"unconfirmed"}')
        raise router.AgentHTTPError(409, "busy", json.dumps({
            "code": "model_lifecycle_busy", "activeOperation": "model_activation"}))

    monkeypatch.setattr(router, "request_agent_json", request)
    with pytest.raises(router.HTTPException):
        router._request_agent_download({})
    assert calls == [True]
    assert clock["sleeps"] == []


def test_uncertain_error_after_busy_is_not_reclassified_as_safe_refusal(monkeypatch, clock):
    import routers.models as router

    calls: list[float] = []

    def request(*_args, timeout, **_kwargs):
        calls.append(timeout)
        if len(calls) == 1:
            clock["now"] += 20.0
            raise _busy(router)
        raise router.AgentUnavailable("response was lost")

    monkeypatch.setattr(router, "request_agent_json", request)
    with pytest.raises(router.HTTPException) as raised:
        router._request_agent_download({})
    assert raised.value.status_code == 503
    assert not raised.value.headers
    assert calls == [30.0, 9.5]


def test_delete_uses_its_own_grace_and_refusal_words(monkeypatch, clock):
    import routers.models as router

    calls = []

    def request(_method, path, *, timeout, **_kwargs):
        calls.append((path, timeout))
        clock["now"] += 2.8
        raise _busy(router)

    monkeypatch.setattr(router, "request_agent_json", request)
    with pytest.raises(router.HTTPException) as raised:
        router._request_agent_waiting_short_holds("/v1/model/delete", {}, "this model cannot be deleted yet", grace_seconds=3.0)
    assert calls == [("/v1/model/delete", 3.0)]
    assert clock["now"] == pytest.approx(3.0)
    assert "this model cannot be deleted yet" in raised.value.detail["message"]
