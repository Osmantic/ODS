"""Rendered library installation and opt-in authenticated log-store lifecycle."""

import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/victorialogs"


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("VLOGS_") and key != "BIND_ADDRESS"}
    env["VLOGS_PASSWORD"] = secrets.token_hex(24) + "$:'\"%#[]"
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "victorialogs",
    ], env=env, capture_output=True, text=True, check=check, timeout=30)


@pytest.mark.parametrize("port", ["9428", "19428"])
def test_catalog_installation_renders_local_authenticated_storage(tmp_path, compose_env, port):
    compose_env["VLOGS_PORT"] = port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["victorialogs"]
    assert service["ports"][0]["published"] == port
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["ports"][0]["target"] == 9428
    assert service["environment"]["VL_httpAuth_username"] == "ods"
    # Compose escapes dollars in its serialized plan; the live round trip below
    # verifies that the actual credential still contains the original characters.
    assert service["environment"]["VL_httpAuth_password"].replace("$$", "$") == compose_env["VLOGS_PASSWORD"]
    assert service["environment"]["VL_retentionPeriod"] == "7d"
    assert "-envflag.enable=true" in service["command"]
    assert "-envflag.prefix=VL_" in service["command"]
    assert "-storageDataPath=/storage" in service["command"]
    assert len(service["volumes"]) == 1
    assert service["volumes"][0]["type"] == "bind"
    assert service["volumes"][0]["source"] == str(tmp_path / "data/victorialogs")
    assert service["volumes"][0]["target"] == "/storage"
    manifest = yaml.safe_load((EXTENSION / "manifest.yaml").read_text())
    assert manifest["service"]["health"] == "/health"
    assert manifest["service"]["ui_path"] == "/select/vmui/"
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "victorialogs")
    assert entry["features"][0]["launch"]["service"] == "victorialogs"
    password = next(item for item in entry["env_vars"] if item["key"] == "VLOGS_PASSWORD")
    assert password["required"]
    declared_password = next(item for item in manifest["service"]["env_vars"]
                             if item["key"] == "VLOGS_PASSWORD")
    assert declared_password["secret"]


@pytest.mark.parametrize("empty", [True, False])
def test_missing_password_prevents_activation(tmp_path, compose_env, empty):
    if empty:
        compose_env["VLOGS_PASSWORD"] = ""
    else:
        del compose_env["VLOGS_PASSWORD"]
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0
    assert "VLOGS_PASSWORD" in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_VLOGS") != "1", reason="Opt-in real VictoriaLogs lifecycle")
def test_live_ingestion_queries_recreation_and_credential_rotation(tmp_path, compose_env):
    project = "ods-q40-vlogs-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["victorialogs"]
    service["container_name"] = project
    service["restart"] = "no"
    service["ports"] = [{"target": 9428, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"}]
    service["volumes"] = [{"type": "volume", "source": "validation-data", "target": "/storage"}]
    service["labels"] = {"io.ods.quality40.validation": "true"}
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    plan["volumes"] = {"validation-data": {"name": project + "-data"}}
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args):
        result = subprocess.run(args, env=compose_env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def base_url():
        container = json.loads(run("docker", "inspect", project))[0]
        binding = container["NetworkSettings"]["Ports"]["9428/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "http://127.0.0.1:" + binding["HostPort"]

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(base, path, password=None, body=None, expected=200):
        headers = {"Content-Type": "application/stream+json"}
        if password is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + password).encode()).decode()
        req = urllib.request.Request(base + path, data=body, headers=headers)
        try:
            response = opener.open(req, timeout=20)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            assert response.status == expected, f"{path}: HTTP {response.status}"
            return response.read()

    password = compose_env["VLOGS_PASSWORD"]
    rotated = secrets.token_hex(24)
    messages = ["ready: thử nghiệm", "failed\nwith detail"]
    payload = b"\n".join(json.dumps({"_msg": message, "service": "fixture", "level": level}).encode()
                          for message, level in zip(messages, ["info", "error"])) + b"\n"
    ingest = "/insert/jsonline?_stream_fields=service"
    query = "/select/logsql/query?" + urllib.parse.urlencode({"query": "service:fixture"})
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "60")
        base = base_url()
        assert request(base, "/health") == b"OK"
        request(base, query, expected=401)
        request(base, ingest, body=payload, expected=401)
        request(base, "/select/vmui/", expected=401)
        request(base, query, password="incorrect", expected=401)
        request(base, ingest, password, payload)
        request(base, ingest, password,
                b'{"_time":"2000-01-01T00:00:00Z","_msg":"expired","service":"fixture"}\n')
        request(base, "/internal/force_flush", password)
        rows = [json.loads(line) for line in request(base, query, password).splitlines()]
        assert sorted(row["_msg"] for row in rows) == sorted(messages)
        filtered = "/select/logsql/query?" + urllib.parse.urlencode({"query": "service:fixture level:error"})
        errors = [json.loads(line) for line in request(base, filtered, password).splitlines()]
        assert [row["_msg"] for row in errors] == [messages[1]]
        assert b"<html" in request(base, "/select/vmui/", password).lower()
        run(*command, "stop", "--timeout", "30")
        service["environment"]["VL_httpAuth_password"] = rotated
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60")
        base = base_url()
        request(base, query, password, expected=401)
        request(base, ingest, password, payload, expected=401)
        preserved = [json.loads(line) for line in request(base, query, rotated).splitlines()]
        assert sorted(row["_msg"] for row in preserved) == sorted(messages)
        request(base, ingest, rotated, b'{"_msg":"after rotation","service":"fixture"}\n')
        request(base, "/internal/force_flush", rotated)
        assert len(request(base, query, rotated).splitlines()) == 3
        logs = subprocess.run(["docker", "logs", project], capture_output=True, text=True,
                              check=True, timeout=20)
        assert password not in logs.stdout + logs.stderr
        assert rotated not in logs.stdout + logs.stderr
        print("Authenticated ingestion/LogsQL/UI; Unicode/newline preservation; recreation and read/write credential rotation passed")
    finally:
        run(*command, "down", "--volumes", "--timeout", "30")
