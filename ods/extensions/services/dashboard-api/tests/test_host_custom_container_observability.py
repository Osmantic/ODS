"""Exercise declared container identities through the authenticated host API."""

import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from test_host_agent import _mod


@pytest.fixture
def installation(tmp_path, monkeypatch):
    builtins = tmp_path / "extensions"
    users = tmp_path / "user-extensions"
    builtins.mkdir()
    users.mkdir()
    monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtins)
    monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", users)
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "container-wire-test")
    monkeypatch.setattr(_mod, "_service_health_cache", (0.0, None))
    shipped = Path(__file__).resolve().parents[4] / "extensions/library/services/librechat/manifest.yaml"
    manifest = yaml.safe_load(shipped.read_text())

    def declare(root, name, service_type="docker"):
        directory = root / "librechat"
        directory.mkdir(exist_ok=True)
        definition = dict(manifest, service=dict(manifest["service"]))
        definition["service"].update(container_name=name, type=service_type)
        (directory / "manifest.yaml").write_text(yaml.safe_dump(definition))

    return builtins, users, declare


@pytest.fixture
def host_api():
    server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def docker_snapshot(monkeypatch, names):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[0] == "docker"
        if command[1] == "ps":
            output = "\n".join(names)
        elif command[1] == "inspect":
            output = json.dumps([
                {"Name": "/" + name, "Config": {"Labels": {}},
                 "State": {"Status": "running", "Health": {"Status": "healthy"}}}
                for name in command[2:]
            ])
        else:
            assert command[1] == "stats"
            output = "\n".join(json.dumps({
                "name": name, "cpu": "12.5%", "mem_usage": "128MiB / 1GiB",
                "mem_percent": "12.5%", "pids": "3",
            }) for name in names)
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(_mod.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("endpoint", ["stats", "health"])
@pytest.mark.parametrize("container_name", ["local-chat-runtime", "ods-renamed-chat"])
def test_declared_container_is_reported_with_manifest_identity(
    installation, host_api, monkeypatch, endpoint, container_name,
):
    builtins, _, declare = installation
    declare(builtins, container_name)
    calls = docker_snapshot(monkeypatch, [container_name, "ods-librechat-mongodb", "unrelated-db"])
    url = f"{host_api}/v1/service/{endpoint}"
    with pytest.raises(urllib.error.HTTPError) as denied:
        urllib.request.urlopen(url, timeout=2)
    assert denied.value.code == 401
    assert calls == []

    request = urllib.request.Request(url, headers={"Authorization": "Bearer container-wire-test"})
    with urllib.request.urlopen(request, timeout=2) as response:
        payload = json.load(response)
    by_name = {row["container_name"]: row for row in payload["containers"]}
    assert set(by_name) == {container_name, "ods-librechat-mongodb"}
    assert by_name[container_name]["service_id"] == "librechat"
    assert by_name["ods-librechat-mongodb"]["service_id"] == "librechat-mongodb"
    if endpoint == "stats":
        assert by_name[container_name]["cpu_percent"] == 12.5
        assert by_name[container_name]["memory_used_mb"] == 128
    else:
        assert by_name[container_name]["health"] == "healthy"
        assert "unrelated-db" not in calls[-1]


@pytest.mark.parametrize("endpoint", ["stats", "health"])
@pytest.mark.parametrize("override_type", ["docker", "cli"])
def test_user_manifest_replaces_builtin_container_declaration(
    installation, host_api, monkeypatch, endpoint, override_type,
):
    builtins, users, declare = installation
    declare(builtins, "previous-chat")
    declare(users, "current-chat", override_type)
    docker_snapshot(monkeypatch, ["previous-chat", "current-chat", "unrelated-db"])
    request = urllib.request.Request(
        f"{host_api}/v1/service/{endpoint}",
        headers={"Authorization": "Bearer container-wire-test"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        payload = json.load(response)
    expected = ["current-chat"] if override_type == "docker" else []
    assert [row["container_name"] for row in payload["containers"]] == expected
