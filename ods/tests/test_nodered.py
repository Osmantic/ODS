"""Native Node-RED deployment, HTTP-node authentication and credential recovery."""

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

import bcrypt
import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/nodered"
REQUIRED = ("NODERED_ADMIN_HASH", "NODERED_HTTP_HASH", "NODERED_CREDENTIAL_SECRET")


def hashed(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=8)).decode()


def render(tmp_path, values, bind=None, port="1880", check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("NODERED_") and key != "BIND_ADDRESS"}
    env.update(values, NODERED_PORT=port)
    if bind is not None:
        env["BIND_ADDRESS"] = bind
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port", ["1880", "11880"])
def test_plan_declares_both_auth_boundaries_and_durable_state(tmp_path, bind, port):
    values = {key: secrets.token_hex(16) for key in REQUIRED}
    plan = json.loads(render(tmp_path, values, bind, port).stdout)
    service = plan["services"]["nodered"]
    assert [(item["target"], item["host_ip"], item["published"]) for item in service["ports"]] == [(1880, bind or "127.0.0.1", port)]
    assert service["user"] == "1000:1000" and service["read_only"] is True
    assert service["volumes"][0]["target"] == "/data"
    assert service["entrypoint"][-1] == "--no-telemetry"
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "nodered")
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == set(REQUIRED)


@pytest.mark.parametrize("missing", REQUIRED)
@pytest.mark.parametrize("empty", [False, True])
def test_missing_or_empty_auth_configuration_rejects_activation(tmp_path, missing, empty):
    values = {key: secrets.token_hex(16) for key in REQUIRED if key != missing}
    if empty:
        values[missing] = ""
    result = render(tmp_path, values, check=False)
    assert result.returncode != 0 and missing in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_NODERED") != "1", reason="Opt-in real Node-RED lifecycle")
def test_native_deployment_credentials_context_rotation_and_cold_restore(tmp_path):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 1000 storage")
    admin_password, http_password, fixture_password = [secrets.token_hex(16) for _ in range(3)]
    values = {"NODERED_ADMIN_HASH": hashed(admin_password), "NODERED_HTTP_HASH": hashed(http_password),
              "NODERED_CREDENTIAL_SECRET": secrets.token_hex(32)}
    project = "ods-q20-nodered-" + uuid.uuid4().hex[:12]
    data = tmp_path / "data/nodered"
    data.mkdir(parents=True)
    os.chown(data, 1000, 1000)
    plan = json.loads(render(tmp_path, values).stdout)
    service = plan["services"]["nodered"]
    service.update(container_name=project, restart="no", labels={"io.ods.quality20.validation": "true"})
    service["ports"][0]["published"] = "0"
    for volume in service["volumes"][1:]:
        volume["source"] = str(EXTENSION / Path(volume["source"]).name)
    fixture = tmp_path / "receipt.js"
    fixture.write_text('''const http = require("node:http");
const expected = "Basic " + Buffer.from("fixture:" + process.env.FIXTURE_PASSWORD).toString("base64");
http.createServer((req, res) => {
  if (req.headers.authorization !== expected) { res.writeHead(401); res.end("Unauthorized"); return; }
  res.writeHead(200, {"Content-Type":"application/json"});
  res.end(JSON.stringify({receipt:"Xin chào from native credentials"}));
}).listen(9090, "0.0.0.0");
''')
    plan["services"]["fixture"] = {
        "image": service["image"], "container_name": project + "-fixture", "user": "1000:1000", "read_only": True,
        "entrypoint": ["node", "/receipt.js"], "environment": {"FIXTURE_PASSWORD": fixture_password},
        "volumes": [{"type": "bind", "source": str(fixture), "target": "/receipt.js", "read_only": True}],
        "networks": ["ods-network"], "labels": {"io.ods.quality20.validation": "true"},
        "healthcheck": {"disable": True},
    }
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def http(path, token=None, method="GET", payload=None, expected=200, password=None):
        binding = json.loads(run("docker", "inspect", project).stdout)[0]["NetworkSettings"]["Ports"]["1880/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        headers = {"Content-Type": "application/json", "Node-RED-API-Version": "v2"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if password is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(("workflow:" + password).encode()).decode()
        request = urllib.request.Request(f"http://127.0.0.1:{binding['HostPort']}{path}", method=method,
                                         data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            response = opener.open(request, timeout=40)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {body[:300]!r}"
            if not body:
                return None
            return json.loads(body) if response.headers.get_content_type() == "application/json" else body.decode()

    def login(password):
        return http("/admin/auth/token", method="POST", payload={"client_id": "node-red-admin", "grant_type": "password", "scope": "*",
                                                               "username": "ods", "password": password})["access_token"]

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        http("/admin/flows", expected=401)
        http("/admin/auth/token", method="POST", payload={"client_id": "node-red-admin", "grant_type": "password", "scope": "*",
                                                          "username": "ods", "password": "incorrect"}, expected=403)
        token = login(admin_password)
        settings = http("/admin/settings", token)
        assert settings["httpNodeRoot"] == "/flows/"
        current = http("/admin/flows", token)
        flows = [
            {"id": "tab", "type": "tab", "label": "ODS native qualification"},
            {"id": "in", "type": "http in", "z": "tab", "url": "/count", "method": "get", "wires": [["count"]]},
            {"id": "count", "type": "function", "z": "tab", "func": "const n=(flow.get('count')||0)+1; flow.set('count',n); msg.payload={count:n, text:'Xin chào'}; return msg;", "outputs": 1, "wires": [["out"]]},
            {"id": "out", "type": "http response", "z": "tab", "wires": []},
            {"id": "secret-in", "type": "http in", "z": "tab", "url": "/receipt", "method": "get", "wires": [["request"]]},
            {"id": "request", "type": "http request", "z": "tab", "url": "http://fixture:9090/receipt", "method": "GET", "ret": "obj",
             "authType": "basic", "credentials": {"user": "fixture", "password": fixture_password}, "wires": [["out"]]},
        ]
        deployed = http("/admin/flows", token, "POST", {"rev": current["rev"], "flows": flows})
        assert deployed["rev"] != current["rev"]
        http("/admin/flows", token, "POST", {"rev": current["rev"], "flows": []}, expected=409)
        http("/flows/count", expected=401)
        http("/flows/count", password="incorrect", expected=401)
        assert http("/flows/count", password=http_password) == {"count": 1, "text": "Xin chào"}
        assert http("/flows/receipt", password=http_password) == {"receipt": "Xin chào from native credentials"}
        credentials = data / "flows_cred.json"
        saved = credentials.read_bytes()
        assert fixture_password.encode() not in saved and "$" in json.loads(saved)
        assert fixture_password not in json.dumps(http("/admin/flows", token))
        print("Separate native authentication, revision conflict and encrypted flow credentials verified", flush=True)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120", "nodered")
        assert http("/flows/count", password=http_password)["count"] == 2
        assert http("/flows/receipt", password=http_password)["receipt"] == "Xin chào from native credentials"
        http("/admin/auth/revoke", token, "POST", {"token": token}, expected=200)
        http("/admin/flows", token, expected=401)
        rotated_admin, rotated_http = secrets.token_hex(16), secrets.token_hex(16)
        service["environment"].update(NODERED_ADMIN_HASH=hashed(rotated_admin).replace("$", "$$"),
                                      NODERED_HTTP_HASH=hashed(rotated_http).replace("$", "$$"))
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120", "nodered")
        http("/admin/auth/token", method="POST", payload={"client_id": "node-red-admin", "grant_type": "password", "scope": "*",
                                                          "username": "ods", "password": admin_password}, expected=403)
        token = login(rotated_admin)
        http("/flows/count", password=http_password, expected=401)
        assert http("/flows/count", password=rotated_http)["count"] == 3
        assert http("/flows/receipt", password=rotated_http)["receipt"] == "Xin chào from native credentials"
        print("Graceful recreation preserves context; revoked session and old passwords stay rejected", flush=True)
        run(*command, "stop", "--timeout", "45", "nodered")
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 1000, 1000)
        service["volumes"][0]["source"] = str(restored)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120", "nodered")
        token = login(rotated_admin)
        assert http("/flows/count", password=rotated_http)["count"] == 4
        assert http("/flows/receipt", password=rotated_http)["receipt"] == "Xin chào from native credentials"
        current = http("/admin/flows", token)
        http("/admin/flows", token, "POST", {"rev": current["rev"], "flows": []})
        http("/flows/count", password=rotated_http, expected=404)
        print("Cold restore preserves executable flows, context and usable encrypted credentials", flush=True)
    finally:
        print(run(*command, "logs", "--tail", "15", "nodered").stdout)
        run(*command, "down", "--volumes")
