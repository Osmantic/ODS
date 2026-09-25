import asyncio

import pytest

from models import ServiceStatus
from routers import extensions


@pytest.mark.parametrize("container,expected", [
    ({"state": "running", "health": "healthy"}, "healthy"),
    ({"state": "running", "health": "unhealthy"}, "unhealthy"),
    ({"state": "running", "health": "none"}, "degraded"),
    ({"state": "running", "health": "starting"}, "degraded"),
    ({"state": "exited", "health": "healthy"}, "down"),
    (None, "unknown"),
])
def test_non_http_readiness_requires_running_container_and_healthcheck(monkeypatch, container, expected):
    rows = [{"service_id": "broker", **container}] if container else []
    monkeypatch.setattr(extensions, "request_agent_json", lambda *a, **k: {
        "schema_version": "ods.host-service-health.v1", "containers": rows,
    })
    statuses = {"broker": ServiceStatus(id="broker", name="Broker", port=1883, external_port=1883, status="healthy")}
    asyncio.run(extensions._inspect_non_http_user_services({"broker": {"port": 1883, "health": ""}}, statuses))
    assert statuses["broker"].status == expected


@pytest.mark.parametrize("snapshot", [
    {"schema_version": "wrong", "containers": []},
    {"schema_version": "ods.host-service-health.v1", "containers": "bad"},
    {"schema_version": "ods.host-service-health.v1", "containers": [
        {"service_id": "broker", "state": "running", "health": "healthy"},
        {"service_id": "broker", "state": "exited", "health": "none"},
    ]},
])
def test_bad_or_ambiguous_snapshot_cannot_confirm_readiness(monkeypatch, snapshot):
    monkeypatch.setattr(extensions, "request_agent_json", lambda *a, **k: snapshot)
    statuses = {}
    asyncio.run(extensions._inspect_non_http_user_services({"broker": {"port": 1883}}, statuses))
    assert statuses["broker"].status == "unknown"


def test_host_unavailable_does_not_fabricate_healthy(monkeypatch):
    def unavailable(*args, **kwargs):
        raise extensions.AgentUnavailable("offline")
    monkeypatch.setattr(extensions, "request_agent_json", unavailable)
    statuses = {}
    asyncio.run(extensions._inspect_non_http_user_services({"broker": {"port": 1883}}, statuses))
    assert statuses["broker"].status == "unknown"


def test_http_and_oneshot_extensions_do_not_trigger_host_probe(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("unnecessary host probe")
    monkeypatch.setattr(extensions, "request_agent_json", unexpected)
    statuses = {}
    asyncio.run(extensions._inspect_non_http_user_services({
        "http": {"port": 8080, "health": "/health"},
        "cli": {"port": 0, "health": ""},
    }, statuses))
    assert statuses == {}
