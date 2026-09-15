"""Dagu deployment plan and opt-in native YAML execution and run-history restore."""

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
EXTENSION = ROOT / "extensions/library/services/dagu"
REQUIRED = ("DAGU_ADMIN_PASSWORD", "DAGU_TOKEN_SECRET")


def render(tmp_path, values, bind=None, port="8525", check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("DAGU_") and key != "BIND_ADDRESS"}
    env.update(values, DAGU_PORT=port)
    if bind is not None:
        env["BIND_ADDRESS"] = bind
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port", ["8525", "18525"])
def test_plan_uses_local_execution_and_private_control_surfaces(tmp_path, bind, port):
    values = {key: secrets.token_hex(16) for key in REQUIRED}
    plan = json.loads(render(tmp_path, values, bind, port).stdout)
    service = plan["services"]["dagu"]
    assert [(item["target"], item["host_ip"], item["published"]) for item in service["ports"]] == [(8080, bind or "127.0.0.1", port)]
    assert service["user"] == "1000:1000" and service["read_only"] is True
    assert service["volumes"][0]["target"] == "/var/lib/dagu"
    assert service["environment"]["DAGU_DEFAULT_EXECUTION_MODE"] == "local"
    assert service["environment"]["DAGU_AUTH_MODE"] == "builtin"
    assert service["environment"]["DAGU_COORDINATOR_ENABLED"] == "false"
    assert service["environment"]["DAGU_TERMINAL_ENABLED"] == "false"
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "dagu")
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == set(REQUIRED)


@pytest.mark.parametrize("missing", REQUIRED)
@pytest.mark.parametrize("empty", [False, True])
def test_bootstrap_and_token_secrets_are_required(tmp_path, missing, empty):
    values = {key: secrets.token_hex(16) for key in REQUIRED if key != missing}
    if empty:
        values[missing] = ""
    result = render(tmp_path, values, check=False)
    assert result.returncode != 0 and missing in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_DAGU") != "1", reason="Opt-in real Dagu lifecycle")
def test_native_jobs_roles_run_receipts_rotation_and_cold_restore(tmp_path):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 1000 storage")
    values = {key: "Aa1!" + secrets.token_hex(16) for key in REQUIRED}
    project = "ods-q20-dagu-" + uuid.uuid4().hex[:12]
    data = tmp_path / "data/dagu"
    data.mkdir(parents=True)
    os.chown(data, 1000, 1000)
    plan = json.loads(render(tmp_path, values).stdout)
    service = plan["services"]["dagu"]
    service.update(container_name=project, restart="no", labels={"io.ods.quality20.validation": "true"})
    service["ports"][0]["published"] = "0"
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=240)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def http(path, token=None, method="GET", payload=None, expected=200):
        binding = json.loads(run("docker", "inspect", project).stdout)[0]["NetworkSettings"]["Ports"]["8080/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(f"http://127.0.0.1:{binding['HostPort']}/api/v1{path}", method=method,
                                         data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            response = opener.open(request, timeout=75)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {body[:300]!r}"
            if not body:
                return None
            return json.loads(body) if response.headers.get_content_type() == "application/json" else body.decode()

    def login(password, username="ods"):
        return http("/auth/login", method="POST", payload={"username": username, "password": password})["token"]

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        http("/health")
        http("/dags", expected=401)
        http("/auth/login", method="POST", payload={"username": "ods", "password": "incorrect"}, expected=401)
        admin = login(values["DAGU_ADMIN_PASSWORD"])
        assert http("/dags", admin)["dags"] == []
        unavailable = http("/users", admin, "POST", {"username": "reader", "password": "Aa1!" + secrets.token_hex(16), "role": "viewer"}, expected=403)
        assert "license" in unavailable["message"].lower()
        viewer = http("/api-keys", admin, "POST", {"name": "ODS read receipts", "role": "viewer", "allowedSurfaces": ["rest_api"],
                                                 "attributionClass": "service_account", "serviceAccountName": "ods-reader"}, expected=201)
        viewer_token = viewer["key"]
        http("/users", viewer_token, expected=403)
        spec = """name: ods-fixture
type: graph
steps:
  - name: produce
    command: sh -c 'printf "Xin chào" > /var/lib/dagu/receipt.txt'
  - name: consume
    command: cat /var/lib/dagu/receipt.txt
    depends: [produce]
"""
        http("/dags", viewer_token, "POST", {"name": "denied", "spec": spec}, expected=403)
        http("/dags", admin, "POST", {"name": "ods-fixture", "spec": spec}, expected=201)
        http("/dags/ods-fixture/start", viewer_token, "POST", {}, expected=403)
        key = http("/api-keys", admin, "POST", {"name": "ODS local runner", "role": "operator", "allowedSurfaces": ["rest_api"],
                                              "attributionClass": "service_account", "serviceAccountName": "ods-fixture"}, expected=201)
        operator = key["key"]
        http("/dags", operator, "POST", {"name": "denied", "spec": spec}, expected=403)
        # The pinned version retains this synchronous endpoint. Production clients
        # should use /start and consume status/SSE; this test avoids custom polling.
        receipt = http("/dags/ods-fixture/start-sync", operator, "POST", {"timeout": 60, "dagRunId": "ods-success"})["dagRun"]
        assert receipt["statusLabel"] == "succeeded"
        assert [node["step"]["name"] for node in receipt["nodes"]] == ["produce", "consume"]
        assert all(node["statusLabel"] == "succeeded" for node in receipt["nodes"])
        assert (data / "receipt.txt").read_text() == "Xin chào"
        assert http("/dag-runs/ods-fixture/ods-success", viewer_token)["dagRunDetails"]["statusLabel"] == "succeeded"
        http("/dags/ods-fixture/start-sync", operator, "POST", {"timeout": 60, "dagRunId": "ods-success"}, expected=409)
        failing_spec = """name: ods-failure
type: graph
steps:
  - name: fail
    command: sh -c 'exit 7'
  - name: must-not-run
    command: touch /var/lib/dagu/should-not-exist
    depends: [fail]
"""
        http("/dags", admin, "POST", {"name": "ods-failure", "spec": failing_spec}, expected=201)
        failed = http("/dags/ods-failure/start-sync", operator, "POST", {"timeout": 60, "dagRunId": "ods-failed"})["dagRun"]
        assert failed["statusLabel"] == "failed"
        assert not (data / "should-not-exist").exists()
        assert failed["nodes"][0]["error"]
        sockets = run("docker", "exec", project, "cat", "/proc/net/tcp", "/proc/net/tcp6").stdout
        listeners = [(line.split()[1].split(":")[0], int(line.split()[1].split(":")[1], 16)) for line in sockets.splitlines()
                     if len(line.split()) > 3 and line.split()[3] == "0A"]
        assert not any(port == 50055 for _, port in listeners)
        assert all(address in ("0100007F", "00000000000000000000000001000000") for address, port in listeners if port == 8090)
        print("Native job dependencies, failure receipt, duplicate run ID and viewer/operator boundaries verified", flush=True)
        rotated = "Aa1!" + secrets.token_hex(16)
        http("/auth/change-password", admin, "POST", {"currentPassword": values["DAGU_ADMIN_PASSWORD"], "newPassword": rotated})
        http("/auth/login", method="POST", payload={"username": "ods", "password": values["DAGU_ADMIN_PASSWORD"]}, expected=401)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        admin = login(rotated)
        http("/auth/login", method="POST", payload={"username": "ods", "password": values["DAGU_ADMIN_PASSWORD"]}, expected=401)

        def saved_receipts():
            for name, run_id, expected in (("ods-fixture", "ods-success", "succeeded"), ("ods-failure", "ods-failed", "failed")):
                result = http(f"/dag-runs/{name}/{run_id}", operator)["dagRunDetails"]
                assert result["statusLabel"] == expected and result["nodes"]
                if name == "ods-fixture":
                    consumer = next(node for node in result["nodes"] if node["step"]["name"] == "consume")
                    log = Path(service["volumes"][0]["source"]) / Path(consumer["stdout"]).relative_to("/var/lib/dagu")
                    assert "Xin chào" in log.read_text()
            assert (Path(service["volumes"][0]["source"]) / "receipt.txt").read_text() == "Xin chào"

        saved_receipts()
        run(*command, "stop", "--timeout", "45")
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 1000, 1000)
        service["volumes"][0]["source"] = str(restored)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        admin = login(rotated)
        saved_receipts()
        http("/api-keys/" + key["apiKey"]["id"], admin, "DELETE", expected=204)
        http("/dags", operator, expected=401)
        http("/api-keys/" + viewer["apiKey"]["id"], admin, "DELETE", expected=204)
        http("/dags", viewer_token, expected=401)
        print("Native password rotation, bootstrap preservation, history cold restore and token revocation verified", flush=True)
    finally:
        print(run(*command, "logs", "--tail", "20").stdout)
        run(*command, "down", "--volumes")
