"""VictoriaMetrics installation, authenticated I/O, and persistent-store contracts."""

import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/victoriametrics"


@pytest.fixture
def compose_env(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    (tmp_path / "empty.env").write_text("")
    env = {key: value for key, value in os.environ.items() if not key.startswith("VMETRICS_")}
    env["VMETRICS_PASSWORD"] = secrets.token_hex(24) + "$:'\"%#[]"
    return env


def render(tmp_path, env, check=True):
    return subprocess.run([
        "docker", "compose", "--env-file", str(tmp_path / "empty.env"),
        "--project-directory", str(tmp_path), "-f", str(EXTENSION / "compose.yaml"),
        "config", "--format", "json", "victoriametrics",
    ], env=env, capture_output=True, text=True, check=check, timeout=30)


@pytest.mark.parametrize("port", ["8428", "18428"])
def test_catalog_targets_the_authenticated_persistent_service(tmp_path, compose_env, port):
    compose_env["VMETRICS_PORT"] = port
    service = json.loads(render(tmp_path, compose_env).stdout)["services"]["victoriametrics"]
    manifest = yaml.safe_load((EXTENSION / "manifest.yaml").read_text())
    assert service["ports"][0]["published"] == port
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["environment"]["VM_httpAuth_username"] == "ods"
    assert service["environment"]["VM_httpAuth_password"]
    assert "-envflag.enable=true" in service["command"]
    assert "-envflag.prefix=VM_" in service["command"]
    assert "-storageDataPath=/storage" in service["command"]
    assert service["volumes"][0]["source"] == str(tmp_path / "data/victoriametrics")
    assert service["volumes"][0]["target"] == "/storage"
    assert manifest["service"]["default_host"] == "victoriametrics"
    assert manifest["service"]["health"] == "/health"
    assert manifest["service"]["ui_path"] == "/vmui/"
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "victoriametrics")
    assert entry["features"][0]["launch"]["service"] == "victoriametrics"
    assert next(item for item in entry["env_vars"] if item["key"] == "VMETRICS_PASSWORD")["required"]


@pytest.mark.parametrize("empty", [True, False])
def test_missing_password_blocks_service_start(tmp_path, compose_env, empty):
    if empty:
        compose_env["VMETRICS_PASSWORD"] = ""
    else:
        del compose_env["VMETRICS_PASSWORD"]
    result = render(tmp_path, compose_env, check=False)
    assert result.returncode != 0
    assert "VMETRICS_PASSWORD" in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_VMETRICS") != "1", reason="Opt-in real VictoriaMetrics lifecycle")
def test_live_import_query_recreation_and_password_rotation(tmp_path, compose_env):
    project = "ods-q40-vmetrics-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, compose_env).stdout)
    service = plan["services"]["victoriametrics"]
    service["container_name"] = project
    service["restart"] = "no"
    service["ports"] = [{"target": 8428, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"}]
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
                                capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def base_url():
        container = json.loads(run("docker", "inspect", project))[0]
        binding = container["NetworkSettings"]["Ports"]["8428/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "http://127.0.0.1:" + binding["HostPort"]

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(base, path, password=None, body=None, expected=200):
        headers = {"Content-Type": "text/plain"}
        if password is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + password).encode()).decode()
        req = urllib.request.Request(base + path, data=body, headers=headers)
        try:
            response = opener.open(req, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            assert response.status == expected, f"{path}: HTTP {response.status}"
            return response.read()

    password = compose_env["VMETRICS_PASSWORD"]
    stamp = int(time.time() * 1000)
    query = "/api/v1/query?" + urllib.parse.urlencode({"query": "ods_quality_sample", "time": stamp / 1000, "nocache": 1})
    sample = f'ods_quality_sample{{service="fixture"}} 7 {stamp}\n'.encode()
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "90")
        base = base_url()
        assert request(base, "/health") == b"OK"
        request(base, query, expected=401)
        request(base, "/api/v1/import/prometheus", body=sample, expected=401)
        request(base, query, password="incorrect", expected=401)
        request(base, "/api/v1/import/prometheus", password, sample, expected=204)
        request(base, "/internal/force_flush", password)
        value = json.loads(request(base, query, password))
        assert value["status"] == "success"
        assert value["data"]["result"][0]["value"][1] == "7"
        assert b"<html" in request(base, "/vmui/", password).lower()
        run(*command, "stop", "--timeout", "30")
        rotated = secrets.token_hex(24)
        service["environment"]["VM_httpAuth_password"] = rotated
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        base = base_url()
        request(base, query, password, expected=401)
        preserved = json.loads(request(base, query, rotated))
        assert preserved["data"]["result"][0]["value"][1] == "7"
        print("Anonymous/wrong credentials rejected; sample imported and queried; recreation retained sample; rotated password enforced")
    finally:
        logs = subprocess.run(["docker", "logs", "--tail", "25", project], capture_output=True, text=True, timeout=20)
        print("Service diagnostics:", logs.stdout, logs.stderr)
        run(*command, "down", "--volumes", "--timeout", "30")
