"""A failed start shows the host agent's own reason on the extension card.

The Extensions page shows the extension-progress ``error``. The host agent
answers a failed /v1/extension/start with its reason in the error body: a
host port another program holds (Docker Desktop could not publish Whisper on
127.0.0.1:9000 while a native Windows app listened there), or a prepare step
such as Hermes' route files. The enable route used to write "Host agent
failed to start extension." in its place.
"""

import json
from unittest.mock import Mock

import pytest

from host_agent_client import AgentHTTPError, AgentUnavailable
from routers import extensions

PORT_REASON = (
    "Host port 9000 is already in use, so whisper could not start. Set WHISPER_PORT in .env "
    "to a free port (ods config edit), or stop the program using port 9000, then retry.\n"
    "Error response from daemon: ports are not available: exposing port TCP 127.0.0.1:9000 "
    "-> 127.0.0.1:0: /forwards/expose returned unexpected status: 500")
HERMES_REASON = "Could not read or write Hermes route files; check installation permissions"
UNREACHABLE_REASON = "Host agent could not be reached. Check that ODS and Docker are running, then retry."


@pytest.fixture
def installation(monkeypatch, tmp_path):
    bundled = tmp_path / "bundled"
    for name in ("whisper", "hermes"):
        directory = bundled / name
        directory.mkdir(parents=True)
        (directory / "manifest.yaml").write_text(
            f"schema_version: ods.services.v1\nservice:\n  id: {name}\n  name: {name}\n"
            "  port: 8000\n  depends_on: []\n")
        (directory / "compose.yaml.disabled").write_text(
            f"services:\n  {name}:\n    image: alpine:3.22\n")

    def select(action, service_ids, expected_sha256=None):
        assert action == "enable"
        for name in service_ids:
            disabled = bundled / name / "compose.yaml.disabled"
            if disabled.exists():
                disabled.rename(bundled / name / "compose.yaml")
        return {"action": "enabled", "service_ids": service_ids}

    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", bundled)
    monkeypatch.setattr(extensions, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(extensions, "_call_agent_hook", Mock(return_value=True))
    monkeypatch.setattr(extensions, "_select_extensions_on_host", select)
    monkeypatch.setattr(extensions, "_call_agent_invalidate_compose_cache", Mock())
    return tmp_path


def _host_answers(monkeypatch, error):
    """The host agent refuses every start and stop with ``error``."""
    requests = []

    def host(method, path, *, payload=None, timeout=None):
        requests.append((method, path, payload))
        assert path in ("/v1/extension/start", "/v1/extension/stop")
        raise error

    monkeypatch.setattr(extensions, "request_agent_json", host)
    return requests


def _enable(test_client, root, service, *, selected):
    if selected:  # Retry on the card: the definition is already selected.
        directory = root / "bundled" / service
        (directory / "compose.yaml.disabled").rename(directory / "compose.yaml")
    response = test_client.post(f"/api/extensions/{service}/enable",
                                headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()["failed_services"] == [service]
    return json.loads((root / "extension-progress" / f"{service}.json").read_text())


@pytest.mark.parametrize("service,reason", [("whisper", PORT_REASON), ("hermes", HERMES_REASON)],
                         ids=["taken-port", "hermes-route"])
@pytest.mark.parametrize("selected", [False, True], ids=["added", "retried"])
def test_card_shows_the_host_agents_reason(test_client, installation, monkeypatch,
                                           service, reason, selected):
    requests = _host_answers(monkeypatch, AgentHTTPError(500, reason, json.dumps({"error": reason})))

    progress = _enable(test_client, installation, service, selected=selected)

    assert requests == [("POST", "/v1/extension/start", {"service_id": service})]
    assert progress["status"] == "error"
    assert progress["error"] == reason


@pytest.mark.parametrize("selected", [False, True], ids=["added", "retried"])
def test_unreachable_host_agent_has_safe_platform_neutral_guidance(test_client, installation,
                                                                  monkeypatch, selected):
    _host_answers(monkeypatch, AgentUnavailable("Host agent POST /v1/extension/start is unreachable"))

    progress = _enable(test_client, installation, "whisper", selected=selected)

    assert progress["status"] == "error"
    assert progress["error"] == UNREACHABLE_REASON
    assert "/v1/extension/start" not in progress["error"]
    assert "ods restart" not in progress["error"]


def test_an_earlier_refusal_is_not_shown_for_a_later_start(test_client, installation, monkeypatch):
    # A refused stop leaves its reason unread; the next start fails before
    # the host answers, so the card must not show the stop's reason.
    _host_answers(monkeypatch, AgentHTTPError(500, "Container stop timed out"))
    assert extensions._call_agent("stop", "whisper") is False
    _host_answers(monkeypatch, AgentUnavailable("Host agent POST /v1/extension/start is unreachable"))

    progress = _enable(test_client, installation, "whisper", selected=True)

    assert progress["error"] == UNREACHABLE_REASON
    assert "Container stop timed out" not in progress["error"]
