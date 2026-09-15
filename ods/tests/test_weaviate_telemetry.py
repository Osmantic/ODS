"""Verify the installed Weaviate definition against its actual telemetry boundary."""

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "extensions/library/services/weaviate/compose.yaml"
COLLECTOR = '''
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

events = []
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return
    def do_GET(self):
        body = json.dumps(events).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/reset":
            events.clear()
        else:
            events.append(json.loads(body))
        self.send_response(200)
        self.end_headers()
HTTPServer(("0.0.0.0", 18080), Handler).serve_forever()
'''
PROBE = '''
import json, os, urllib.request, urllib.error

def request(path, method="GET", body=None, authenticated=True):
    headers = {"Content-Type": "application/json"}
    if authenticated:
        headers["Authorization"] = "Bearer " + os.environ["WEAVIATE_API_KEY"]
    req = urllib.request.Request("http://weaviate:8080" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, response.read()

status, _ = request("/v1/objects", authenticated=False)
assert status in (401, 403), status
if os.environ["ODS_WEAVIATE_SEED"] == "1":
    status, body = request("/v1/schema", "POST", {
        "class": "OdsTelemetryProbe", "vectorizer": "none",
        "properties": [{"name": "text", "dataType": ["text"]}],
    })
    assert status == 200, (status, body)
    status, body = request("/v1/objects", "POST", {
        "class": "OdsTelemetryProbe", "id": "cb3458e1-73af-4a76-a8a2-201c4f6f684d",
        "properties": {"text": "local synthetic vector"}, "vector": [1.0, 0.0, 0.0],
    })
    assert status == 200, (status, body)
status, body = request("/v1/graphql", "POST", {
    "query": '{ Get { OdsTelemetryProbe(nearVector: {vector: [1,0,0]}, limit: 1) { text } } }',
})
assert status == 200, (status, body)
payload = json.loads(body)
assert not payload.get("errors"), payload
assert payload["data"]["Get"]["OdsTelemetryProbe"][0]["text"] == "local synthetic vector", payload
print("Authenticated stored-vector search passed; anonymous access rejected")
'''


@pytest.fixture
def rendered(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required")
    empty = tmp_path / "empty.env"
    empty.write_text("")
    env = dict(os.environ)
    env["WEAVIATE_API_KEY"] = secrets.token_hex(24)
    env.pop("BIND_ADDRESS", None)
    command = ["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
               "-f", str(COMPOSE), "config", "--format", "json"]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), env, tmp_path


def test_installed_weaviate_disables_vendor_telemetry(rendered):
    plan, _, _ = rendered
    service = plan["services"]["weaviate"]
    assert service["environment"].get("DISABLE_TELEMETRY") == "true"
    assert service["environment"]["AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED"] == "false"
    assert service["environment"]["AUTHENTICATION_APIKEY_ENABLED"] == "true"
    assert {port["target"] for port in service["ports"]} == {8080, 50051}
    assert all(port["host_ip"] == "127.0.0.1" for port in service["ports"])


@pytest.mark.skipif(os.getenv("ODS_TEST_WEAVIATE_TELEMETRY") != "1",
                    reason="Opt-in actual image and isolated telemetry collector")
def test_actual_image_telemetry_stops_without_losing_vector_data(rendered):
    plan, env, directory = rendered
    project = "ods-q40-weaviate-" + uuid.uuid4().hex[:12]
    service = plan["services"]["weaviate"]
    service.update(container_name=project, restart="no")
    service.pop("ports")
    plan["networks"]["ods-network"] = {"internal": True}
    plan["volumes"] = {"data": {"name": project + "-data"}}
    service["volumes"] = [{"type": "volume", "source": "data", "target": "/var/lib/weaviate"}]
    candidate = service["environment"].get("DISABLE_TELEMETRY")
    service["environment"].pop("DISABLE_TELEMETRY", None)
    service["environment"].update(TELEMETRY_URL="http://collector:18080/telemetry",
                                  TELEMETRY_PUSH_INTERVAL="1s")
    plan["services"]["collector"] = {
        "image": "python:3.13-alpine",
        "container_name": project + "-collector",
        "command": ["python", "-u", "-c", COLLECTOR],
        "networks": {"ods-network": None},
        "healthcheck": {"test": ["CMD", "python", "-c",
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18080', timeout=2).read()"],
            "interval": "1s", "timeout": "3s", "retries": 20},
    }
    service["depends_on"] = {"collector": {"condition": "service_healthy"}}
    config = directory / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, check=True, timeout=180):
        result = subprocess.run(args, env=env, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=timeout)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    def events():
        result = run("docker", "exec", project + "-collector", "python", "-c",
                     "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:18080', timeout=5).read().decode())")
        return json.loads(result.stdout)

    def search(seed):
        result = run("docker", "exec", "-e", "WEAVIATE_API_KEY", "-e",
                     "ODS_WEAVIATE_SEED=" + ("1" if seed else "0"),
                     project + "-collector", "python", "-c", PROBE)
        print(result.stdout)

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        search(seed=True)
        baseline = events()
        assert baseline, "The shipped default must reach the local collector before the control is tested"
        print("Default telemetry event types:", sorted({event["type"] for event in baseline}))
        run(*command, "stop", "-t", "30", "weaviate")
        baseline = events()
        assert any(event["type"] == "INIT" for event in baseline), baseline
        assert any(event["type"] == "TERMINATE" for event in baseline), baseline
        run("docker", "exec", project + "-collector", "python", "-c",
            "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:18080/reset', data=b''), timeout=5).read()")
        assert candidate == "true"
        service["environment"]["DISABLE_TELEMETRY"] = candidate
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--no-deps", "--wait",
            "--wait-timeout", "120", "weaviate")
        search(seed=False)
        # A bounded observation window, not a retry: the control runs with a
        # one-second interval and the positive baseline proves the collector.
        time.sleep(4)
        assert events() == []
        run(*command, "stop", "-t", "30", "weaviate")
        assert events() == [], "Shutdown must not send a final telemetry payload"
        print("Actual Weaviate startup/periodic/shutdown telemetry disabled; persisted search and authentication preserved")
    finally:
        diagnostics = run("docker", "logs", "--tail", "8", project, check=False)
        print("Weaviate diagnostics:", diagnostics.stdout, diagnostics.stderr)
        run(*command, "down", "--volumes", "--timeout", "30")
