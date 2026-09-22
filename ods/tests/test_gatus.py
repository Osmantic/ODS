"""Native Gatus config, local credential generation and opt-in status-history lifecycle."""

import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/gatus"


def password_hash(password):
    bcrypt = pytest.importorskip("bcrypt")
    return base64.urlsafe_b64encode(bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))).decode()


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GATUS_")}
    env.update(GATUS_PASSWORD_BCRYPT=password_hash(secrets.token_hex(20)), BIND_ADDRESS="127.0.0.1")
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "gatus",
    ], env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind_address", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
def test_bind_address_opt_in_preserves_native_configuration(tmp_path, compose_env, bind_address):
    compose_env.pop("BIND_ADDRESS", None)
    baseline = json.loads(render(tmp_path, compose_env).stdout)["services"]["gatus"]
    if bind_address is not None:
        compose_env["BIND_ADDRESS"] = bind_address
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["gatus"]
    assert all(port["host_ip"] == (bind_address or "127.0.0.1") for port in service["ports"])
    assert [(p["published"], p["target"]) for p in service["ports"]] == [
        (p["published"], p["target"]) for p in baseline["ports"]]
    assert service["environment"] == baseline["environment"]
    assert service["volumes"] == baseline["volumes"]


@pytest.mark.parametrize("port", ["8102", "18102"])
def test_local_persistent_status_plan(tmp_path, compose_env, port):
    compose_env["GATUS_PORT"] = port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["gatus"]
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["ports"][0]["published"] == port
    assert service["user"] == "1000:1000" and service["read_only"]
    assert "healthcheck" not in service  # Native scratch image has no HTTP probe executable.
    config = next(v for v in service["volumes"] if v["target"] == "/config/config.yaml")
    assert config["source"] == str(tmp_path / "config/gatus/config.yaml") and config["read_only"]
    assert config["bind"]["create_host_path"] is False
    native = yaml.safe_load((EXTENSION / "config/gatus/config.yaml").read_text())
    assert native["storage"]["type"] == "sqlite" and native["storage"]["path"] == "/data/gatus.db"
    assert native["security"]["basic"]["password-bcrypt-base64"] == "${GATUS_PASSWORD_BCRYPT}"
    assert native["metrics"] is False and "alerting" not in native and "remote" not in native
    assert [(e["url"], e["conditions"]) for e in native["endpoints"]] == [
        ("http://open-webui:8080/health", ["[STATUS] == 200"]),
        ("http://llama-server:8080/health", ["[STATUS] == 200"]),
    ]
    for service_id in ("open-webui", "llama-server"):
        manifest = yaml.safe_load((ROOT / "extensions/services" / service_id / "manifest.yaml").read_text())["service"]
        assert manifest["default_host"] == service_id and manifest["port"] == 8080 and manifest["health"] == "/health"


@pytest.mark.parametrize("value", [None, ""])
def test_missing_hash_blocks_activation(tmp_path, compose_env, value):
    if value is None:
        del compose_env["GATUS_PASSWORD_BCRYPT"]
    else:
        compose_env["GATUS_PASSWORD_BCRYPT"] = value
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0 and "GATUS_PASSWORD_BCRYPT" in result.stderr


@pytest.mark.parametrize("password,confirmation,valid", [
    ("riêng_tư_" + "x" * 20, "riêng_tư_" + "x" * 20, True),
    ("short", "short", False), ("x" * 73, "x" * 73, False),
    ("x" * 24, "different" * 3, False),
])
def test_native_hash_generator_public_cli(password, confirmation, valid):
    bcrypt = pytest.importorskip("bcrypt")
    result = subprocess.run([sys.executable, str(EXTENSION / "hash-password.py")],
                            input=password + "\n" + confirmation + "\n", text=True,
                            capture_output=True, timeout=10, start_new_session=True)
    assert password not in result.stdout + result.stderr
    if valid:
        value = result.stdout.strip()
        assert result.returncode == 0 and len(value) == 80
        assert bcrypt.checkpw(password.encode(), base64.urlsafe_b64decode(value))
    else:
        assert result.returncode != 0 and not result.stdout


@pytest.mark.skipif(os.getenv("ODS_TEST_GATUS") != "1", reason="Opt-in native status history lifecycle")
def test_live_status_auth_failure_recovery_rotation_and_cold_restore(tmp_path, compose_env):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Host-bind qualification prepares isolated UID 1000 storage")
    password, rotated = [secrets.token_hex(20) for _ in range(2)]
    compose_env["GATUS_PASSWORD_BCRYPT"] = password_hash(password)
    project = "ods-q40-gatus-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["gatus"]
    service.update(container_name=project, restart="no")
    service["ports"][0]["published"] = "0"
    service["labels"] = {"io.ods.quality40.validation": "true"}
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    data = tmp_path / "data/gatus"
    data.mkdir(parents=True)
    os.chown(data, 1000, 1000)
    # Preserve native quoting around environment expansion as well as URLs/conditions.
    native = (EXTENSION / "config/gatus/config.yaml").read_text().replace("interval: 30s", "interval: 2s")
    native_path = tmp_path / "config/gatus/config.yaml"
    native_path.parent.mkdir(parents=True)
    native_path.write_text(native)
    native_path.chmod(0o644)
    source = tmp_path / "source"
    source.mkdir()
    source.chmod(0o755)
    healthy = source / "health"
    healthy.write_text('{"status":"ok"}')
    healthy.chmod(0o644)
    plan["services"]["target"] = {
        "container_name": project + "-target", "command": [],
        "image": "codeberg.org/readeck/readeck:0.23.2@sha256:32bd82e457a3a9a3fffb161f55cfebf0fd7ffbc87ddf2fe7a321361905348f0a",
        "entrypoint": ["/bin/busybox", "httpd", "-f", "-p", "8080", "-h", "/www"],
        "user": "1000:1000", "read_only": True, "restart": "no",
        "volumes": [{"type": "bind", "source": str(source), "target": "/www", "read_only": True}],
        "networks": {"ods-network": {"aliases": ["open-webui", "llama-server"]}},
        "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
    }
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def run(*args):
        result = subprocess.run(args, env=compose_env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout + result.stderr if args[:2] == ("docker", "logs") else result.stdout

    def url():
        state = json.loads(run("docker", "inspect", project))[0]
        assert state["State"]["Running"], run("docker", "logs", project)
        binding = state["NetworkSettings"]["Ports"]["8080/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "http://127.0.0.1:" + binding["HostPort"]

    def request(base, path, credential=None, expected=200):
        headers = {}
        if credential:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + credential).encode()).decode()
        req = urllib.request.Request(base + path, headers=headers)
        try:
            response = opener.open(req, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{path}: {response.status}: {body[:350]!r}"
            return body

    def statuses(base, credential):
        return json.loads(request(base, "/api/v1/endpoints/statuses", credential))

    def latest(item):
        return max(item["results"], key=lambda result: result["timestamp"])

    def wait_for_status(base, credential, success):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            items = statuses(base, credential)
            if len(items) == 2 and all(item["results"] and latest(item)["success"] is success for item in items):
                return items
            time.sleep(1)
        pytest.fail(f"Both native targets failed to reach success={success}: {items!r}")

    try:
        run(*command, "up", "-d", "target")
        target_state = json.loads(run("docker", "inspect", project + "-target"))[0]["State"]
        assert target_state["Running"], run("docker", "logs", project + "-target")
        assert '"status":"ok"' in run("docker", "exec", project + "-target", "/bin/busybox", "wget", "-qO-", "-T", "3", "http://127.0.0.1:8080/health")
        run(*command, "up", "-d", "gatus")
        base = url()
        request(base, "/health")
        assert b"<html" in request(base, "/").lower()
        request(base, "/api/v1/endpoints/statuses", expected=401)
        request(base, "/api/v1/endpoints/statuses", "wrong", expected=401)
        initial = wait_for_status(base, password, True)
        assert {item["key"] for item in initial} == {"ods_chat", "ods_inference"}
        # Native public endpoints remain public even with Basic auth configured.
        public_config = json.loads(request(base, "/api/v1/config"))
        assert public_config and compose_env["GATUS_PASSWORD_BCRYPT"].encode() not in request(base, "/api/v1/config")
        assert b"<svg" in request(base, "/api/v1/endpoints/ods_chat/health/badge.svg")
        healthy.rename(source / "offline")
        failed = wait_for_status(base, password, False)
        failed_times = {item["key"]: latest(item)["timestamp"] for item in failed}
        (source / "offline").rename(healthy)
        recovered = wait_for_status(base, password, True)
        assert all(any(not result["success"] for result in item["results"]) for item in recovered)
        service["environment"]["GATUS_PASSWORD_BCRYPT"] = password_hash(rotated)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "gatus")
        base = url()
        request(base, "/api/v1/endpoints/statuses", password, expected=401)
        restored = wait_for_status(base, rotated, True)
        assert all(any(result["timestamp"] == failed_times[item["key"]] for result in item["results"]) for item in restored)
        run(*command, "stop", "gatus")
        backup = tmp_path / "cold-backup"
        shutil.copytree(data, backup)
        shutil.copy2(native_path, tmp_path / "config-backup.yaml")
        native_path.rename(tmp_path / "config-before-restore.yaml")
        shutil.copy2(tmp_path / "config-backup.yaml", native_path)
        data.rename(tmp_path / "before-restore")
        shutil.copytree(backup, data)
        for path in [data, *data.rglob("*")]:
            os.chown(path, 1000, 1000)
        run(*command, "up", "-d", "--force-recreate", "gatus")
        base = url()
        restored = wait_for_status(base, rotated, True)
        assert all(any(result["timestamp"] == failed_times[item["key"]] and not result["success"] for result in item["results"]) for item in restored)
        # Valid base64 containing an invalid bcrypt value fails authentication closed.
        service["environment"]["GATUS_PASSWORD_BCRYPT"] = base64.urlsafe_b64encode(b"x" * 60).decode()
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "gatus")
        base = url()
        request(base, "/api/v1/endpoints/statuses", rotated, expected=401)
        request(base, "/api/v1/endpoints/statuses", expected=401)
        # Malformed base64 stops the native process rather than disabling auth.
        service["environment"]["GATUS_PASSWORD_BCRYPT"] = "!" * 80
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "gatus")
        assert int(run("docker", "wait", project).strip()) != 0
        assert "base64" in run("docker", "logs", project)
        service["environment"]["GATUS_PASSWORD_BCRYPT"] = password_hash(rotated)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "gatus")
        base = url()
        assert len(statuses(base, rotated)) == 2
        logs = run("docker", "logs", project)
        assert password not in logs and rotated not in logs and service["environment"]["GATUS_PASSWORD_BCRYPT"] not in logs
    finally:
        run(*command, "down", "--volumes", "--remove-orphans")
