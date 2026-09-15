"""Native fixed-route delivery receipts without sending any external message."""

import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import urllib.error
import urllib.request
import uuid

import bcrypt
import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/apprise"
REQUIRED = ("APPRISE_AUTH_HASH", "APPRISE_CONFIG_TEXT", "APPRISE_DJANGO_SECRET")


def render(tmp_path, values, bind=None, port="8094", check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("APPRISE_") and key != "BIND_ADDRESS"}
    env.update(values, APPRISE_PORT=port)
    if bind is not None:
        env["BIND_ADDRESS"] = bind
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port", ["8094", "18094"])
def test_plan_keeps_raw_api_in_gateway_namespace(tmp_path, bind, port):
    plan = json.loads(render(tmp_path, dict.fromkeys(REQUIRED, "configured"), bind, port).stdout)
    gateway, api = plan["services"]["apprise"], plan["services"]["apprise-api"]
    assert [(item["target"], item["host_ip"], item["published"]) for item in gateway["ports"]] == [(8080, bind or "127.0.0.1", port)]
    assert not api.get("ports") and not api.get("networks")
    assert api["network_mode"] == "service:apprise"
    assert api["user"] == "1000:1000" and api["read_only"] is True
    assert api["environment"]["APPRISE_CONFIG_LOCK"] == "yes"
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "apprise")
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == set(REQUIRED)


@pytest.mark.parametrize("missing", REQUIRED)
@pytest.mark.parametrize("empty", [False, True])
def test_credentials_and_destinations_are_required(tmp_path, missing, empty):
    values = {key: "configured" for key in REQUIRED if key != missing}
    if empty:
        values[missing] = ""
    result = render(tmp_path, values, check=False)
    assert result.returncode != 0 and missing in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_APPRISE") != "1", reason="Opt-in native Apprise with local-only receiver")
def test_native_fixed_routes_receipts_rotation_and_recovery(tmp_path):
    password = secrets.token_urlsafe(24)

    def hashed(value):
        return bcrypt.hashpw(value.encode(), bcrypt.gensalt(rounds=8)).decode()

    routes = "\n".join(route + " = json://apprise-receiver:9876/" + route + "?retry=0&cto=3&rto=5" for route in ("ok", "fail", "redirect"))
    values = {"APPRISE_AUTH_HASH": hashed(password), "APPRISE_CONFIG_TEXT": routes, "APPRISE_DJANGO_SECRET": secrets.token_urlsafe(32)}
    project = "ods-q20-apprise-" + uuid.uuid4().hex[:12]
    plan = json.loads(render(tmp_path, values).stdout)
    for name, service in plan["services"].items():
        service.update(container_name=project + "-" + name, restart="no", labels={"io.ods.quality20.validation": "true"})
        for mount in service.get("volumes", []):
            mount["source"] = str(EXTENSION / Path(mount["source"]).name)
    gateway, api = plan["services"]["apprise"], plan["services"]["apprise-api"]
    gateway["ports"][0]["published"] = "0"
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    receiver = tmp_path / "receiver.py"
    receiver.write_text('''import json
from http.server import BaseHTTPRequestHandler, HTTPServer
events = []
class Receiver(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return
    def do_GET(self):
        if self.path not in ("/events", "/health"):
            events.append({"path": self.path, "method": "GET"})
        body = json.dumps(events if self.path == "/events" else {"ready": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        events.append({"path": self.path, "payload": payload})
        self.send_response(500 if self.path == "/fail" else 302 if self.path == "/redirect" else 200)
        if self.path == "/redirect":
            self.send_header("Location", "http://apprise-receiver:9876/should-not-follow")
        self.end_headers()
        self.wfile.write(b"fixture response")
HTTPServer(("0.0.0.0", 9876), Receiver).serve_forever()
''')
    receiver.chmod(0o644)
    plan["services"]["apprise-receiver"] = {
        "image": api["image"], "container_name": project + "-receiver", "entrypoint": ["python", "/receiver.py"],
        "user": "1000:1000", "read_only": True, "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
        "volumes": [{"type": "bind", "source": str(receiver), "target": "/receiver.py", "read_only": True}],
        "tmpfs": ["/config", "/attach", "/plugin"], "networks": {"ods-network": {}},
        "ports": [{"target": 9876, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"}],
        "healthcheck": {"test": ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9876/health', timeout=3)"], "interval": "5s", "timeout": "4s", "retries": 3},
        "labels": {"io.ods.quality20.validation": "true"},
    }
    config = tmp_path / "isolated.json"

    def save():
        config.write_text(json.dumps(plan))
        config.chmod(0o600)

    save()
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, check=True):
        result = subprocess.run(args, capture_output=True, text=True, timeout=180)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def http(path, method="GET", payload=None, expected=200, credential=None, fixture=False):
        name, port = (project + "-receiver", "9876/tcp") if fixture else (project + "-apprise", "8080/tcp")
        binding = json.loads(run("docker", "inspect", name).stdout)[0]["NetworkSettings"]["Ports"][port][0]
        assert binding["HostIp"] == "127.0.0.1"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if credential:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + credential).encode()).decode()
        request = urllib.request.Request("http://127.0.0.1:" + binding["HostPort"] + path, method=method,
                                         data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            response = opener.open(request, timeout=40)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {body[:400]!r}"
            if expected == 401:
                assert response.headers["WWW-Authenticate"] == 'Basic realm="ODS Apprise"'
            return json.loads(body) if body and response.headers.get_content_type() == "application/json" else body.decode()

    message = {"title": "ODS workflow", "body": "Xin chào: artifact 42", "tag": "ok"}

    def deliver():
        before = len(http("/events", fixture=True))
        result = http("/notify/ods", "POST", {**message, "urls": "json://apprise-receiver:9876/evil"}, credential=password)
        assert result["error"] is None
        events = http("/events", fixture=True)
        assert len(events) == before + 1 and events[-1]["path"] == "/ok"
        assert events[-1]["payload"]["message"] == message["body"]

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        status = http("/status")
        assert status["config_lock"] is True and status["attach_lock"] is True
        http("/notify/ods", "POST", message, expected=401)
        http("/notify/ods", "POST", message, expected=401, credential="incorrect")
        assert http("/events", fixture=True) == []
        http("/notify/ods", "POST", {**message, "attachment": "http://apprise-receiver:9876/attachment"}, expected=400, credential=password)
        assert http("/events", fixture=True) == []
        deliver()
        http("/notify/ods", "POST", {**message, "tag": "fail"}, expected=424, credential=password)
        assert http("/events", fixture=True)[-1]["path"] == "/fail"
        http("/notify/ods", "POST", {**message, "tag": "redirect"}, expected=424, credential=password)
        events = http("/events", fixture=True)
        assert [event["path"] for event in events] == ["/ok", "/fail", "/redirect"]
        http("/notify/ods", "POST", {}, expected=400, credential=password)
        for path in ("/notify", "/notify/other", "/cfg/ods", "/get/ods", "/add/ods", "/del/ods", "/json/urls/ods", "/details", "/"):
            http(path, "POST", message, expected=404, credential=password)
        assert run("docker", "exec", project + "-apprise-api", "stat", "-c", "%a:%u:%g", "/tmp/apprise/config/ods.cfg").stdout.strip() == "600:1000:1000"
        listeners = run("docker", "exec", project + "-apprise-api", "cat", "/proc/net/tcp").stdout.splitlines()[1:]
        assert any(line.split()[1] == "0100007F:1F40" and line.split()[3] == "0A" for line in listeners)
        assert not any(line.split()[1] == "00000000:1F40" and line.split()[3] == "0A" for line in listeners)
        print("Fixed native target, Unicode receipt, role challenge, downstream 424 and no redirect/attachment or target override verified", flush=True)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120", "apprise", "apprise-api")
        deliver()
        old_password, password = password, secrets.token_urlsafe(24)
        gateway["environment"]["APPRISE_AUTH_HASH"] = hashed(password).replace("$", "$$")
        save()
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120", "apprise", "apprise-api")
        http("/notify/ods", "POST", message, expected=401, credential=old_password)
        deliver()
        api["environment"]["APPRISE_CONFIG_TEXT"] = "this-is-not-an-apprise-url"
        save()
        invalid = run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "45", "apprise-api", check=False)
        assert invalid.returncode != 0
        assert "must contain a usable native text configuration" in run(*command, "logs", "--tail", "20", "apprise-api").stdout
        api["environment"]["APPRISE_CONFIG_TEXT"] = routes
        save()
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120", "apprise-api")
        deliver()
        print("Recreation, gateway namespace reconciliation on hash update, invalid target rejection and configuration recovery verified", flush=True)
    finally:
        print(run(*command, "logs", "--tail", "12").stdout)
        run(*command, "down", "--volumes")
