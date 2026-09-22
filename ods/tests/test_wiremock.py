"""WireMock rendered contract and opt-in real HTTP fixture lifecycle."""

import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/wiremock"


def render(tmp_path, password, bind=None, port="8099", check=True):
    env = {key: value for key, value in os.environ.items() if key not in ("WIREMOCK_PASSWORD", "WIREMOCK_PORT", "BIND_ADDRESS")}
    env["WIREMOCK_PORT"] = port
    if password is not None:
        env["WIREMOCK_PASSWORD"] = password
    if bind is not None:
        env["BIND_ADDRESS"] = bind
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port", ["8099", "18099"])
def test_plan_preserves_admin_auth_persistence_and_operator_binding(tmp_path, bind, port):
    password = secrets.token_hex(24)
    service = json.loads(render(tmp_path, password, bind, port).stdout)["services"]["wiremock"]
    assert service["ports"] == [{"mode": "ingress", "host_ip": bind or "127.0.0.1", "target": 8080, "published": port, "protocol": "tcp"}]
    command = service["command"]
    assert command[command.index("--admin-api-basic-auth") + 1] == "ods:" + password
    assert command[command.index("--max-request-journal-entries") + 1] == "100"
    assert "--disable-response-templating" in command
    assert service["user"] == "1000:1000" and service["read_only"] is True
    mounts = {item["target"]: item for item in service["volumes"]}
    assert mounts["/home/wiremock"]["source"] == str(tmp_path / "data/wiremock")
    assert mounts["/home/wiremock/__files/__ods_status.json"]["read_only"] is True
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "wiremock")
    assert entry["health_endpoint"] == "/__ods_status.json"
    assert next(item for item in entry["env_vars"] if item["key"] == "WIREMOCK_PASSWORD")["required"]


@pytest.mark.parametrize("password", [None, ""])
def test_empty_admin_password_rejects_plan(tmp_path, password):
    result = render(tmp_path, password, check=False)
    assert result.returncode != 0 and "WIREMOCK_PASSWORD" in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_WIREMOCK") != "1", reason="Opt-in real WireMock lifecycle")
def test_real_http_fixtures_auth_persistence_rotation_and_cold_restore(tmp_path):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 1000 host binds")
    password, rotated = secrets.token_hex(24), secrets.token_hex(24)
    project = "ods-q20-wiremock-" + uuid.uuid4().hex[:12]
    shutil.copytree(EXTENSION / "config", tmp_path / "config")
    data = tmp_path / "data/wiremock"
    for path in (data, data / "mappings", data / "__files"):
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, 1000, 1000)
    plan = json.loads(render(tmp_path, password).stdout)
    service = plan["services"]["wiremock"]
    service.update(container_name=project, restart="no", labels={"io.ods.quality20.validation": "true"})
    service["ports"][0]["published"] = "0"
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=150)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def http(path, credential=None, method="GET", payload=None, expected=200):
        binding = json.loads(run("docker", "inspect", project))[0]["NetworkSettings"]["Ports"]["8080/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        headers = {"Content-Type": "application/json"}
        if credential is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + credential).encode()).decode()
        request = urllib.request.Request(f"http://127.0.0.1:{binding['HostPort']}{path}", method=method,
                                         data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        try:
            response = opener.open(request, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {body[:300]!r}"
            return body

    fixture = {"persistent": True, "request": {"method": "GET", "url": "/fixture/status"},
               "response": {"status": 200, "jsonBody": {"ready": True, "text": "Tài liệu"}}}
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "90")
        assert json.loads(http("/__ods_status.json"))["service"] == "wiremock"
        for bad in (None, "incorrect"):
            http("/__admin/mappings", bad, expected=401)
            http("/__admin/mappings", bad, "POST", fixture, expected=401)
        assert b"swagger" in http("/__admin/docs", password).lower()
        created = json.loads(http("/__admin/mappings", password, "POST", fixture, expected=201))
        assert json.loads(http("/fixture/status")) == fixture["response"]["jsonBody"]
        assert list((data / "mappings").glob("*.json"))
        http("/fixture/missing", expected=404)
        failure = {"request": {"method": "GET", "url": "/fixture/failure"}, "response": {"status": 503, "body": "simulated upstream failure"}}
        http("/__admin/mappings", password, "POST", failure, expected=201)
        assert http("/fixture/failure", expected=503) == b"simulated upstream failure"
        journal = json.loads(http("/__admin/requests", password))
        assert any(item["request"]["url"] == "/fixture/status" for item in journal["requests"])
        http("/__admin/requests", password, "DELETE", expected=200)
        assert json.loads(http("/__admin/requests", password))["requests"] == []
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        assert json.loads(http("/fixture/status")) == fixture["response"]["jsonBody"]
        http("/fixture/failure", expected=404)  # Ephemeral fixtures were intentionally not saved.
        service["command"][service["command"].index("--admin-api-basic-auth") + 1] = "ods:" + rotated
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        http("/__admin/mappings", password, expected=401)
        assert json.loads(http("/__admin/mappings", rotated))["meta"]["total"] == 1
        run(*command, "stop", "--timeout", "30")
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 1000, 1000)
        next(item for item in service["volumes"] if item["target"] == "/home/wiremock")["source"] = str(restored)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        assert json.loads(http("/fixture/status")) == fixture["response"]["jsonBody"]
        http("/__admin/mappings/" + created["id"], rotated, "DELETE", expected=200)
        http("/fixture/status", expected=404)
        assert json.loads(run("docker", "inspect", project))[0]["State"]["Health"]["Status"] == "healthy"
    finally:
        print(run(*command, "logs", "--tail", "20"))
        run(*command, "down", "--volumes")
