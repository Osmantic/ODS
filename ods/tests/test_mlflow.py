"""Local recipe checks and opt-in authenticated tracking/artifact lifecycle."""

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
import urllib.parse
import urllib.request
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/mlflow"


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("MLFLOW_")}
    env.update(MLFLOW_ADMIN_PASSWORD=secrets.token_hex(24) + "%:'$#[]",
               MLFLOW_SECRET_KEY=secrets.token_hex(24), BIND_ADDRESS="127.0.0.1")
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "mlflow",
    ], env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind_address", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
def test_bind_address_opt_in_preserves_native_configuration(tmp_path, compose_env, bind_address):
    compose_env.pop("BIND_ADDRESS", None)
    baseline = json.loads(render(tmp_path, compose_env).stdout)["services"]["mlflow"]
    if bind_address is not None:
        compose_env["BIND_ADDRESS"] = bind_address
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["mlflow"]
    assert all(port["host_ip"] == (bind_address or "127.0.0.1") for port in service["ports"])
    assert [(p["published"], p["target"]) for p in service["ports"]] == [
        (p["published"], p["target"]) for p in baseline["ports"]]
    assert service["environment"] == baseline["environment"]
    assert service["volumes"] == baseline["volumes"]


@pytest.mark.parametrize("port", ["5051", "15051"])
def test_recipe_uses_prepared_image_and_persistent_private_storage(tmp_path, compose_env, port):
    compose_env["MLFLOW_PORT"] = port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["mlflow"]
    assert service["image"] == "ods-mlflow:3.16.0-r1"
    assert service["pull_policy"] == "never" and "build" not in service
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["ports"][0]["published"] == port
    assert service["ports"][0]["target"] == 5000
    assert service["user"] == "1000:1000" and service["read_only"]
    assert service["cap_drop"] == ["ALL"]
    assert service["volumes"][0]["source"] == str(tmp_path / "data/mlflow")
    assert service["environment"]["MLFLOW_DISABLE_TELEMETRY"] == "true"
    assert service["environment"]["MLFLOW_BASIC_AUTH_FAIL_CLOSED"] == "true"
    assert service["environment"]["MLFLOW_ENABLE_REMOTE_ASSISTANT"] == "false"
    assert service["environment"]["MLFLOW_ENABLE_ASSISTANT_SANDBOX"] == "true"
    manifest = yaml.safe_load((EXTENSION / "manifest.yaml").read_text())
    assert manifest["service"]["setup_hook"] == "setup.sh"


@pytest.mark.parametrize("key", ["MLFLOW_ADMIN_PASSWORD", "MLFLOW_SECRET_KEY"])
@pytest.mark.parametrize("value", [None, ""])
def test_missing_authentication_configuration_prevents_activation(tmp_path, compose_env, key, value):
    if value is None:
        del compose_env[key]
    else:
        compose_env[key] = value
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0 and key in result.stderr


@pytest.mark.parametrize("key", ["MLFLOW_ADMIN_PASSWORD", "MLFLOW_SECRET_KEY"])
@pytest.mark.parametrize("value", ["short", "x" * 129, "line\n[evil]\nkey=value", "spaces are not accepted here"])
def test_bootstrap_rejects_ambiguous_auth_config_before_startup(compose_env, key, value):
    compose_env[key] = value
    result = subprocess.run(["python3", str(EXTENSION / "entrypoint.py")], env=compose_env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode != 0 and key in result.stderr
    assert value not in result.stderr and "Traceback" not in result.stderr


@pytest.mark.parametrize("exit_code", [0, 23])
def test_installed_setup_builds_the_recipe_image_and_propagates_failure(tmp_path, compose_env, exit_code):
    installed = tmp_path / "data/user-extensions/mlflow"
    shutil.copytree(EXTENSION, installed)
    record = tmp_path / "docker-call.json"
    tools = tmp_path / "fake-bin"
    tools.mkdir()
    docker = tools / "docker"
    docker.write_text(f"#!{sys.executable}\nimport json, pathlib, sys\n"
                      f"pathlib.Path({str(record)!r}).write_text(json.dumps(sys.argv[1:]))\n"
                      f"sys.exit({exit_code})\n")
    docker.chmod(0o755)
    compose_env["PATH"] = str(tools) + os.pathsep + compose_env["PATH"]
    result = subprocess.run(["bash", str(installed / "setup.sh"), str(tmp_path), "cpu"],
                            cwd=tmp_path, env=compose_env, capture_output=True, text=True, timeout=15)
    assert result.returncode == exit_code, result.stderr
    assert json.loads(record.read_text()) == [
        "build", "--tag", "ods-mlflow:3.16.0-r1", "--file", str(installed / "Dockerfile"), str(installed),
    ]


def test_installed_tracking_server_is_accepted_by_stack_resolver(tmp_path, compose_env):
    installed = tmp_path / "data/user-extensions/mlflow"
    shutil.copytree(EXTENSION, installed)
    (tmp_path / "docker-compose.base.yml").write_text("services:\n  fixture-core:\n    image: busybox\n")
    result = subprocess.run(["bash", str(ROOT / "scripts/resolve-compose-stack.sh"),
                             "--script-dir", str(tmp_path), "--gpu-backend", "cpu"],
                            env=compose_env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "data/user-extensions/mlflow/compose.yaml" in result.stdout


@pytest.mark.skipif(os.getenv("ODS_TEST_MLFLOW") != "1", reason="Opt-in real tracking and artifact server")
def test_live_auth_permissions_artifacts_recreation_and_cold_restore(tmp_path, compose_env):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Host-bind qualification prepares isolated UID 1000 storage")
    project = "ods-q40-mlflow-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["mlflow"]
    service["container_name"] = project
    service["restart"] = "no"
    service["ports"][0]["published"] = "0"
    service["labels"] = {"io.ods.quality40.validation": "true"}
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    data = tmp_path / "data/mlflow"
    data.mkdir(parents=True)
    os.chown(data, 1000, 1000)
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]
    password = compose_env["MLFLOW_ADMIN_PASSWORD"]
    observer_password, rotated, new_seed = (secrets.token_hex(24) for _ in range(3))

    def run(*args):
        result = subprocess.run(args, env=compose_env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def base_url():
        state = json.loads(run("docker", "inspect", project))[0]
        binding = state["NetworkSettings"]["Ports"]["5000/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "http://127.0.0.1:" + binding["HostPort"]

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(base, method, path, credential=None, payload=None, expected=200, username="ods"):
        headers = {}
        if credential is not None:
            headers["Authorization"] = "Basic " + base64.b64encode((username + ":" + credential).encode()).decode()
        if payload is not None and not isinstance(payload, bytes):
            payload = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=payload, method=method, headers=headers)
        try:
            response = opener.open(req, timeout=20)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: {response.status}: {body!r}"
            return body

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        base = base_url()
        request(base, "GET", "/health")
        for credential in (None, "incorrect"):
            request(base, "GET", "/", credential, expected=401)
            request(base, "POST", "/api/2.0/mlflow/experiments/create", credential,
                    {"name": "denied"}, expected=401)
        experiment = json.loads(request(base, "POST", "/api/2.0/mlflow/experiments/create", password,
                                        {"name": "Synthetic local experiment"}))["experiment_id"]
        created = json.loads(request(base, "POST", "/api/2.0/mlflow/runs/create", password,
                                     {"experiment_id": experiment, "start_time": int(time.time() * 1000)}))["run"]
        run_id = created["info"]["run_id"]
        run_path = "/api/2.0/mlflow/runs/get?run_id=" + run_id
        request(base, "POST", "/api/2.0/mlflow/runs/log-parameter", password,
                {"run_id": run_id, "key": "dataset", "value": "synthetic local fixture"})
        request(base, "POST", "/api/2.0/mlflow/runs/log-metric", password,
                {"run_id": run_id, "key": "accuracy", "value": 0.875, "timestamp": int(time.time() * 1000), "step": 0})
        uri = urllib.parse.urlsplit(created["info"]["artifact_uri"])
        assert uri.scheme == "mlflow-artifacts", created
        artifact_path = "/api/2.0/mlflow-artifacts/artifacts/" + uri.path.strip("/") + "/report.json"
        artifact = b'{"fixture":"local only","score":0.875}\n'
        request(base, "PUT", artifact_path, password, artifact)
        assert request(base, "GET", artifact_path, password) == artifact
        request(base, "POST", "/api/2.0/mlflow/users/create", password,
                {"username": "observer", "password": observer_password})
        request(base, "GET", run_path, observer_password, username="observer", expected=403)
        request(base, "GET", artifact_path, observer_password, username="observer", expected=403)
        request(base, "POST", "/api/3.0/mlflow/users/permissions/grant", password,
                {"resource_type": "experiment", "resource_id": experiment, "username": "observer", "permission": "READ"})
        assert request(base, "GET", artifact_path, observer_password, username="observer") == artifact
        request(base, "POST", "/api/2.0/mlflow/runs/log-metric", observer_password,
                {"run_id": run_id, "key": "accuracy", "value": 1.0, "timestamp": 1, "step": 1},
                username="observer", expected=403)
        request(base, "PATCH", "/api/2.0/mlflow/users/update-password", password,
                {"username": "ods", "password": rotated, "current_password": password})
        request(base, "GET", run_path, password, expected=401)
        recorded = json.loads(request(base, "GET", run_path, rotated))["run"]
        assert recorded["data"]["params"] == [{"key": "dataset", "value": "synthetic local fixture"}]
        assert recorded["data"]["metrics"][0]["value"] == 0.875
        run(*command, "stop", "--timeout", "30")
        # Cold-copy all auth, tracking and artifact state while no writer runs.
        backup = tmp_path / "restored-data"
        shutil.copytree(data, backup)
        for path in [backup, *backup.rglob("*")]:
            os.chown(path, 1000, 1000)
        service["volumes"][0]["source"] = str(backup)
        service["environment"]["MLFLOW_ADMIN_PASSWORD"] = new_seed
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        base = base_url()
        request(base, "GET", run_path, password, expected=401)
        request(base, "GET", run_path, new_seed, expected=401)
        restored = json.loads(request(base, "GET", run_path, rotated))["run"]
        assert restored["data"] == recorded["data"]
        assert request(base, "GET", artifact_path, rotated) == artifact
        assert request(base, "GET", artifact_path, observer_password, username="observer") == artifact
        print("Experiment metrics, artifact permissions, password rotation and cold-copy restoration passed")
    finally:
        try:
            logs = subprocess.run(["docker", "logs", project], capture_output=True, text=True,
                                  check=True, timeout=20)
            diagnostics = logs.stdout + logs.stderr
            assert all(value not in diagnostics for value in (password, observer_password, rotated, new_seed))
            print("MLflow diagnostics:", diagnostics[-2000:])
        finally:
            run(*command, "down", "--volumes", "--timeout", "30")
