"""OpenObserve recipe and opt-in native HTTP telemetry lifecycle."""

import base64
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/openobserve"
EMAIL = "ods@localhost.invalid"


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("OPENOBSERVE_")}
    env.update(OPENOBSERVE_PASSWORD="Aa1_" + secrets.token_hex(24), BIND_ADDRESS="127.0.0.1")
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "openobserve",
    ], env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind_address", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
def test_bind_address_opt_in_preserves_native_configuration(tmp_path, compose_env, bind_address):
    compose_env.pop("BIND_ADDRESS", None)
    baseline = json.loads(render(tmp_path, compose_env).stdout)["services"]["openobserve"]
    if bind_address is not None:
        compose_env["BIND_ADDRESS"] = bind_address
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["openobserve"]
    assert all(port["host_ip"] == (bind_address or "127.0.0.1") for port in service["ports"])
    assert [(p["published"], p["target"]) for p in service["ports"]] == [
        (p["published"], p["target"]) for p in baseline["ports"]]
    assert service["environment"] == baseline["environment"]
    assert service["volumes"] == baseline["volumes"]


@pytest.mark.parametrize("port", ["5080", "15080"])
def test_local_storage_and_browser_origin_follow_port(tmp_path, compose_env, port):
    compose_env["OPENOBSERVE_PORT"] = port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["openobserve"]
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["ports"][0]["published"] == port
    assert service["ports"][0]["target"] == 5080
    assert service["user"] == "10001:10001" and service["read_only"]
    assert service["volumes"][0]["source"] == str(tmp_path / "data/openobserve")
    env = service["environment"]
    assert env["ZO_WEB_URL"] == "http://localhost:" + port
    assert env["ZO_CORS_ALLOWED_ORIGINS"] == "http://127.0.0.1:" + port
    assert env["ZO_ROOT_USER_EMAIL"] == EMAIL
    assert env["ZO_ROOT_USER_PASSWORD"] == compose_env["OPENOBSERVE_PASSWORD"]
    assert env["ZO_META_STORE"] == "sqlite" and env["ZO_LOCAL_MODE_STORAGE"] == "disk"
    assert env["ZO_GRPC_ADDR"] == "127.0.0.1"
    assert env["ZO_COMPACT_DATA_RETENTION_DAYS"] == "7"
    for key in ("ZO_TELEMETRY", "ZO_RUM_ENABLED", "ZO_TRACING_ENABLED", "ZO_TRACING_SEARCH_ENABLED"):
        assert env[key] == "false"
    # Native distroless image has no shell/HTTP client; ODS owns the HTTP probe.
    assert "healthcheck" not in service


@pytest.mark.parametrize("value", [None, ""])
def test_missing_password_blocks_activation(tmp_path, compose_env, value):
    if value is None:
        del compose_env["OPENOBSERVE_PASSWORD"]
    else:
        compose_env["OPENOBSERVE_PASSWORD"] = value
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0 and "OPENOBSERVE_PASSWORD" in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_OPENOBSERVE") != "1", reason="Opt-in real observability database")
def test_live_ingest_search_auth_rotation_and_cold_restore(tmp_path, compose_env):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Host-bind qualification prepares isolated UID 10001 storage")
    project = "ods-q40-openobserve-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["openobserve"]
    service.update(container_name=project, restart="no")
    service["ports"][0]["published"] = "0"
    service["labels"] = {"io.ods.quality40.validation": "true"}
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    data = tmp_path / "data/openobserve"
    data.mkdir(parents=True)
    os.chown(data, 10001, 10001)
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]
    password = compose_env["OPENOBSERVE_PASSWORD"]
    new_seed, rotated = ["Aa1_" + secrets.token_hex(24) for _ in range(2)]

    def run(*args):
        result = subprocess.run(args, env=compose_env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def ready():
        container = json.loads(run("docker", "inspect", project))[0]
        binding = container["NetworkSettings"]["Ports"]["5080/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        base = "http://127.0.0.1:" + binding["HostPort"]
        deadline = time.monotonic() + 90
        last_status = "not observed"
        # Bounded readiness observation, not a retry of an ingestion/mutation.
        while time.monotonic() < deadline:
            try:
                with opener.open(base + "/healthz", timeout=2) as response:
                    if response.status == 200:
                        return base
            except (urllib.error.URLError, TimeoutError, ConnectionResetError) as exc:
                last_status = str(exc)
            time.sleep(0.25)
        pytest.fail("OpenObserve did not become HTTP-ready: " + last_status)

    def request(base, method, path, credential=None, payload=None, expected=200, cookie=None, headers=None):
        headers = dict(headers or {})
        if credential:
            headers["Authorization"] = "Basic " + base64.b64encode((EMAIL + ":" + credential).encode()).decode()
        if cookie:
            headers["Cookie"] = cookie
        if payload is not None:
            payload = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=payload, method=method, headers=headers)
        try:
            response = opener.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: {response.status}: {body!r}"
            return body, response.headers

    def login(base, secret, expected=200):
        body, headers = request(base, "POST", "/auth/login", payload={"name": EMAIL, "password": secret}, expected=expected)
        if expected != 200:
            return ""
        assert json.loads(body)["status"] is True
        cookies = SimpleCookie()
        cookies.load(headers["Set-Cookie"])
        assert cookies["auth_tokens"]["httponly"] and cookies["auth_tokens"]["samesite"] == "Lax"
        return "auth_tokens=" + cookies["auth_tokens"].value

    now = int(time.time() * 1_000_000)
    logs = [{"_timestamp": now, "message": "ready: tiếng Việt", "level": "info", "duration": 3},
            {"_timestamp": now + 1, "message": "failed\nwith detail", "level": "error", "duration": 7}]

    def search(base, secret, sql, kind="logs"):
        body = request(base, "POST", "/api/default/_search?type=" + kind, secret, {
            "query": {"sql": sql, "start_time": now - 60_000_000, "end_time": now + 60_000_000,
                      "from": 0, "size": 100}, "search_type": "ui",
        })[0]
        return json.loads(body)["hits"]

    try:
        run(*command, "up", "-d")
        base = ready()
        assert b"<html" in request(base, "GET", "/web/")[0].lower()
        for credential in (None, "incorrect"):
            request(base, "GET", "/api/default/streams", credential, expected=401)
            request(base, "POST", "/api/default/fixture/_json", credential, logs, expected=401)
        login(base, "incorrect", expected=401)
        cookie = login(base, password)
        request(base, "GET", "/api/default/streams", cookie=cookie)
        for origin, allowed in (("http://localhost:5080", True), ("https://foreign.invalid", False)):
            _, headers = request(base, "OPTIONS", "/api/default/fixture/_json", headers={
                "Origin": origin, "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            })
            assert (headers.get("Access-Control-Allow-Origin") == origin) is allowed
        ingest = json.loads(request(base, "POST", "/api/default/fixture/_json", password, logs)[0])
        assert ingest["status"][0]["successful"] == 2
        hits = search(base, password, 'SELECT message, level, duration FROM "fixture" ORDER BY duration')
        assert hits == logs  # Native UI search also returns the event timestamp.
        assert search(base, password, 'SELECT sum(duration) AS total FROM "fixture"')[0]["total"] == 10
        metrics = [{"__name__": "fixture_gauge", "__type__": "gauge", "host": "isolated", "_timestamp": now, "value": 42.0}]
        request(base, "POST", "/api/default/ingest/metrics/_json", password, metrics)
        assert search(base, password, 'SELECT value FROM "fixture_gauge"', "metrics")[0]["value"] == 42.0
        trace_id, span_id = "1" * 32, "2" * 16
        traces = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "ods-fixture"}}]},
                  "scopeSpans": [{"scope": {"name": "ods-test"}, "spans": [{"traceId": trace_id, "spanId": span_id,
                  "name": "local-test", "kind": 1, "startTimeUnixNano": str(now * 1000), "endTimeUnixNano": str((now + 1000) * 1000),
                  "status": {"code": 1}}]}]}]}
        request(base, "POST", "/api/default/v1/traces", password, traces)
        assert search(base, password, 'SELECT trace_id FROM "default"', "traces")[0]["trace_id"] == trace_id
        # Fresh bootstrap settings cannot silently reset the persisted root account.
        service["environment"]["ZO_ROOT_USER_PASSWORD"] = new_seed
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate")
        base = ready()
        login(base, new_seed, expected=401)
        request(base, "GET", "/api/default/streams", cookie=cookie)
        request(base, "PUT", "/api/default/users/" + EMAIL, password, {
            "change_password": True, "old_password": password, "new_password": rotated,
        })
        request(base, "GET", "/api/default/streams", password, expected=401)
        request(base, "GET", "/api/default/streams", cookie=cookie, expected=401)
        login(base, rotated)
        run(*command, "stop", "--timeout", "60")
        backup = tmp_path / "restored"
        shutil.copytree(data, backup)
        for path in [backup, *backup.rglob("*")]:
            os.chown(path, 10001, 10001)
        service["volumes"][0]["source"] = str(backup)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate")
        base = ready()
        request(base, "GET", "/api/default/streams", password, expected=401)
        assert len(search(base, rotated, 'SELECT * FROM "fixture"')) == 2
        assert search(base, rotated, 'SELECT value FROM "fixture_gauge"', "metrics")[0]["value"] == 42.0
        assert search(base, rotated, 'SELECT trace_id FROM "default"', "traces")[0]["trace_id"] == trace_id
    finally:
        try:
            result = subprocess.run(["docker", "logs", project], capture_output=True, text=True, check=True, timeout=15)
            diagnostics = result.stdout + result.stderr
            assert all(value not in diagnostics for value in (password, new_seed, rotated))
            print(diagnostics[-4000:])
        finally:
            run(*command, "down", "--volumes", "--timeout", "60")


@pytest.mark.skipif(os.getenv("ODS_TEST_OPENOBSERVE") != "1", reason="Opt-in native bootstrap validation")
@pytest.mark.parametrize("weak", ["Aa1_", "only_lowercase_characters"])
def test_native_bootstrap_rejects_weak_password_without_a_listener(tmp_path, compose_env, weak):
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["openobserve"]
    process_env = dict(compose_env, **service["environment"])
    process_env["ZO_ROOT_USER_PASSWORD"] = weak
    name = "ods-q40-openobserve-invalid-" + uuid.uuid4().hex[:12]
    command = ["docker", "run", "--rm", "--name", name, "--network", "none", "--read-only",
               "--user", "10001:10001", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
               "--memory", "2g", "--cpus", "2", "--tmpfs", "/data:rw,uid=10001,gid=10001,size=128m",
               "--tmpfs", "/tmp:rw,size=128m"]
    for key in service["environment"]:
        command.extend(["--env", key])
    command.append(service["image"])
    try:
        result = subprocess.run(command, env=process_env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "--force", name], check=True, capture_output=True, timeout=15)
        raise
    diagnostics = result.stdout + result.stderr
    assert result.returncode != 0 and "ZO_ROOT_USER_PASSWORD is too weak" in diagnostics
    assert weak not in diagnostics
