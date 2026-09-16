"""Real model-metadata HTTP and host routing; no installed services or cloud."""
import os
if os.name != "posix":
    from unittest import SkipTest
    raise SkipTest("Applied provider runtime requires Linux/WSL")

import copy
import http.client
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pixel_provider import health, host_api
from pixel_provider.config import public_config
from pixel_provider.store import StoreError
from pixel_provider.vault import CredentialStore
from test_connection_transport import server
from test_provider_runtime import configuration
import test_pixel_provider_host_api as host_fixture


@pytest.mark.parametrize("status,body,expected", [
    (200, {"data": [{"id": "chosen"}]}, {"status": "online", "models": 1}),
    (200, {"data": [{"id": "different"}]}, {"status": "offline"}),
    (200, {}, {"status": "unavailable"}),
    (200, {"data": [None]}, {"status": "unavailable"}),
    (302, {}, {"status": "offline"}),
    (401, {"error": "private-sentinel"}, {"status": "offline"}),
])
def test_real_probe_is_fixed_path_bounded_and_does_not_echo_secrets(status, body, expected):
    with server(status, json.dumps(body).encode()) as (connection, calls):
        provider = {"baseUrl": connection["baseUrl"], "model": "chosen"}
        assert health.probe_provider(provider, "fixture-key") == expected
        assert len(calls) == 1
        assert calls[0][0] == "/v1/models"
        assert calls[0][1]["Authorization"] == "Bearer fixture-key"


def test_applied_revision_and_policy_gate_before_contact(tmp_path, monkeypatch):
    root = tmp_path / "pixel-providers"
    root.mkdir(mode=0o700)
    config = configuration()
    config["revision"] = 0
    store = CredentialStore(root)
    store.save_public({"expectedRevision": 0, "document": public_config(config),
                       "credentialChanges": {}})
    runtime = {"status": "applied", "binding": {"revision": 1, "activationId": "fixture"}}
    monkeypatch.setattr(host_api, "runtime_status", lambda _dir: copy.deepcopy(runtime))
    calls = []
    monkeypatch.setattr(health, "probe_provider", lambda provider, credential: calls.append(provider["id"]) or {"status": "online", "models": 1})
    assert health.inspect_active_health(tmp_path) == {"status": "online", "models": 1}
    assert calls == [config["roles"]["leader"]]
    calls.clear()
    runtime["binding"]["revision"] = 2
    assert health.inspect_active_health(tmp_path) == {"status": "unavailable"}
    assert calls == []
    runtime["status"] = "pending"
    assert health.inspect_active_health(tmp_path) == {"status": "unavailable"}
    assert calls == []


def test_changed_route_withdraws_old_probe(tmp_path, monkeypatch):
    root = tmp_path / "pixel-providers"
    root.mkdir(mode=0o700)
    config = configuration()
    config["revision"] = 0
    CredentialStore(root).save_public({"expectedRevision": 0, "document": public_config(config), "credentialChanges": {}})
    runtime = iter([{"status": "applied", "binding": {"revision": 1}}, {"status": "pending"}])
    monkeypatch.setattr(host_api, "runtime_status", lambda _dir: next(runtime))
    monkeypatch.setattr(health, "probe_provider", lambda *args: {"status": "online", "models": 1})
    assert health.inspect_active_health(tmp_path) == {"status": "unavailable"}


def test_supervisor_enforces_overall_deadline_and_never_reports_fake_offline(monkeypatch):
    from pixel_provider import advice_process
    def blocked(command, snapshot, **options):
        assert options["deadline_seconds"] == 8
        assert command[1:3] == ["-I", "-B"]
        raise StoreError("advice-worker-deadline")
    monkeypatch.setattr(health.platform, "system", lambda: "Linux")
    monkeypatch.setattr(advice_process, "run_worker", blocked)
    assert health.health_status("/private-fixture") == {"status": "unavailable"}


def test_real_host_route_authenticates_before_health_probe():
    host_fixture.HostHTTP.setUpClass()
    try:
        with patch.object(health, "health_status", return_value={"status": "online", "models": 2}) as probe:
            for token, expected in [("wrong", 403), ("synthetic-provider-test-key", 200)]:
                client = http.client.HTTPConnection(*host_fixture.HostHTTP.server.server_address, timeout=3)
                try:
                    client.request("GET", "/v1/pixel/providers/health", headers={"Authorization": "Bearer " + token})
                    response = client.getresponse()
                    body = json.loads(response.read())
                    assert response.status == expected
                    if expected == 403:
                        probe.assert_not_called()
                    else:
                        assert body == {"status": "online", "models": 2}
                        assert response.getheader("Cache-Control") == "no-store"
                finally:
                    client.close()
            probe.assert_called_once()
    finally:
        host_fixture.HostHTTP.tearDownClass()
def test_actual_private_worker_on_unconfigured_host_is_read_only(tmp_path):
    assert health.health_status(tmp_path) == {"status": "unavailable"}
    assert list(tmp_path.iterdir()) == []
