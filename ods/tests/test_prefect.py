"""Prefect bootstrap plus opt-in real client flow, CSRF and storage lifecycle."""

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
EXTENSION = ROOT / "extensions/library/services/prefect"
FLOW = '''from prefect import flow, task
from prefect.artifacts import create_markdown_artifact
from prefect.cache_policies import NO_CACHE
from prefect.settings import PREFECT_SERVER_ANALYTICS_ENABLED, PREFECT_CLOUD_ENABLE_ORCHESTRATION_TELEMETRY
assert PREFECT_SERVER_ANALYTICS_ENABLED.value() is False
assert PREFECT_CLOUD_ENABLE_ORCHESTRATION_TELEMETRY.value() is False

@task(persist_result=True, cache_policy=NO_CACHE)
def double(value):
    return value * 2

@flow(name="ods-quality40-local-flow", persist_result=True, log_prints=True)
def pipeline(value):
    result = double(value)
    create_markdown_artifact(key="ods-quality40-proof", markdown=f"Result: {result}")
    print("Synthetic local flow completed")
    return result

state = pipeline(21, return_state=True)
assert state.is_completed()
assert state.result() == 42
print("ODS_FLOW_ID=" + str(state.state_details.flow_run_id))
'''


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PREFECT_")}
    env.update(PREFECT_PASSWORD=secrets.token_hex(24), BIND_ADDRESS="0.0.0.0")
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "prefect",
    ], env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("port", ["4200", "14200"])
def test_recipe_keeps_ui_api_on_same_origin_and_persists_state(tmp_path, compose_env, port):
    compose_env["PREFECT_PORT"] = port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["prefect"]
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["ports"][0]["published"] == port
    assert service["ports"][0]["target"] == 4200
    assert service["user"] == "1000:1000" and service["read_only"]
    assert service["volumes"][0]["source"] == str(tmp_path / "data/prefect")
    env = service["environment"]
    assert env["PREFECT_SERVER_UI_API_URL"] == "/api"
    assert env["PREFECT_SERVER_UI_STATIC_DIRECTORY"] == "/tmp/prefect-ui"
    assert env["PREFECT_SERVER_API_CSRF_PROTECTION_ENABLED"] == "true"
    assert env["PREFECT_SERVER_ANALYTICS_ENABLED"] == "false"
    assert env["PREFECT_CLOUD_ENABLE_ORCHESTRATION_TELEMETRY"] == "false"
    assert env["PREFECT_API_AUTH_STRING"] == env["PREFECT_SERVER_API_AUTH_STRING"] == "ods:" + compose_env["PREFECT_PASSWORD"]


@pytest.mark.parametrize("value", [None, ""])
def test_missing_password_blocks_activation(tmp_path, compose_env, value):
    if value is None:
        del compose_env["PREFECT_PASSWORD"]
    else:
        compose_env["PREFECT_PASSWORD"] = value
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0 and "PREFECT_PASSWORD" in result.stderr


@pytest.mark.parametrize("value", ["short", "x" * 129, "bad\ncredential", "no:ambiguous:value"])
def test_invalid_password_stops_before_server(tmp_path, compose_env, value):
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["prefect"]
    compose_env["PREFECT_PASSWORD"] = value
    result = subprocess.run(["bash", "-euc", service["command"][0].replace("$$", "$")],
                            env=compose_env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1 and "PREFECT_PASSWORD" in result.stderr
    assert value not in result.stderr and "command not found" not in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_PREFECT") != "1", reason="Opt-in real Prefect client/server")
def test_live_csrf_flow_results_rotation_and_cold_restore(tmp_path, compose_env):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Host-bind qualification prepares isolated UID 1000 storage")
    project = "ods-q40-prefect-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["prefect"]
    service.update(container_name=project, restart="no")
    service["ports"][0]["published"] = "0"
    service["labels"] = {"io.ods.quality40.validation": "true"}
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    data = tmp_path / "data/prefect"
    data.mkdir(parents=True)
    os.chown(data, 1000, 1000)
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]
    password, rotated = compose_env["PREFECT_PASSWORD"], secrets.token_hex(24)

    def run(*args, script=None):
        result = subprocess.run(args, env=compose_env, input=script,
                                capture_output=True, text=True, timeout=240)
        assert result.returncode == 0, result.stdout + result.stderr
        assert password not in result.stdout + result.stderr
        assert rotated not in result.stdout + result.stderr
        return result.stdout

    def base_url():
        binding = json.loads(run("docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", project))["4200/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "http://127.0.0.1:" + binding["HostPort"]

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(base, method, path, credential=None, payload=None, expected=200, csrf=None, extra_headers=None):
        headers = dict(extra_headers or {})
        if credential:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + credential).encode()).decode()
        if csrf:
            headers.update({"Prefect-Csrf-Token": csrf["token"], "Prefect-Csrf-Client": csrf["client"]})
        if payload is not None:
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
            assert password.encode() not in body and rotated.encode() not in body
            return body, response.headers

    def csrf_token(base, credential):
        return json.loads(request(base, "GET", "/api/csrf-token?client=" + uuid.uuid4().hex, credential)[0])

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "180")
        base = base_url()
        request(base, "GET", "/api/ready")
        assert b"<html" in request(base, "GET", "/")[0].lower()
        ui = json.loads(request(base, "GET", "/ui-settings")[0])
        assert ui["api_url"] == "/api" and ui["auth"] == "BASIC" and ui["csrf_enabled"] is True
        for credential in (None, "incorrect"):
            request(base, "GET", "/api/admin/settings", credential, expected=401)
        request(base, "POST", "/api/flows/filter", password, {}, expected=403)
        csrf = csrf_token(base, password)
        request(base, "POST", "/api/flows/filter", password, {}, csrf={"token": "incorrect", "client": csrf["client"]}, expected=403)
        assert json.loads(request(base, "POST", "/api/flows/filter", password, {}, csrf=csrf)[0]) == []
        _, headers = request(base, "OPTIONS", "/api/flows/filter", expected=400,
                             extra_headers={"Origin": "https://untrusted.invalid", "Access-Control-Request-Method": "POST"})
        assert "Access-Control-Allow-Origin" not in headers
        output = run("docker", "exec", "-i", project, "python", "-", script=FLOW)
        flow_id = next(line.split("=", 1)[1] for line in output.splitlines() if line.startswith("ODS_FLOW_ID="))
        flow_path = "/api/flow_runs/" + flow_id
        request(base, "GET", flow_path, expected=401)
        record = json.loads(request(base, "GET", flow_path, password)[0])
        assert record["state"]["type"] == "COMPLETED" and record["parameters"] == {"value": 21}
        tasks = json.loads(request(base, "POST", "/api/task_runs/filter", password,
                                   {"flow_runs": {"id": {"any_": [flow_id]}}}, csrf=csrf)[0])
        assert len(tasks) == 1 and tasks[0]["state"]["type"] == "COMPLETED"
        artifacts = json.loads(request(base, "POST", "/api/artifacts/filter", password,
                                       {"artifacts": {"key": {"any_": ["ods-quality40-proof"]}}}, csrf=csrf)[0])
        assert len(artifacts) == 1 and artifacts[0]["data"] == "Result: 42"
        artifact_path = "/api/artifacts/" + artifacts[0]["id"]
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180")
        base = base_url()
        assert json.loads(request(base, "GET", flow_path, password)[0])["state"]["type"] == "COMPLETED"
        assert json.loads(request(base, "GET", artifact_path, password)[0])["data"] == "Result: 42"
        compose_env["PREFECT_PASSWORD"] = rotated
        service["environment"] = json.loads(render(tmp_path, compose_env).stdout)["services"]["prefect"]["environment"]
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180")
        base = base_url()
        request(base, "GET", flow_path, password, expected=401)
        request(base, "POST", "/api/flows/", password, {"name": "denied-old-password"}, csrf=csrf, expected=401)
        assert json.loads(request(base, "GET", artifact_path, rotated)[0])["data"] == "Result: 42"
        run(*command, "stop", "--timeout", "45")
        backup = tmp_path / "restored-state"
        shutil.copytree(data, backup)
        for path in [backup, *backup.rglob("*")]:
            os.chown(path, 1000, 1000)
        service["volumes"][0]["source"] = str(backup)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180")
        base = base_url()
        restored = json.loads(request(base, "GET", flow_path, rotated)[0])
        assert restored["parameters"] == record["parameters"] and restored["state"]["type"] == "COMPLETED"
        assert json.loads(request(base, "GET", artifact_path, rotated)[0])["data"] == "Result: 42"
        restore_probe = f'''import asyncio
from uuid import UUID
from prefect.client.orchestration import get_client
async def main():
    async with get_client() as client:
        run = await client.read_flow_run(UUID({flow_id!r}))
        assert await run.state.result() == 42
asyncio.run(main())
'''
        run("docker", "exec", "-i", project, "python", "-", script=restore_probe)
    finally:
        try:
            logs = subprocess.run(["docker", "logs", project], capture_output=True, text=True, check=True, timeout=20)
            diagnostics = logs.stdout + logs.stderr
            assert password not in diagnostics and rotated not in diagnostics
        finally:
            run(*command, "down", "--volumes", "--timeout", "45")
