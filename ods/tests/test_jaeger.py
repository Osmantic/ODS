"""Jaeger plan contract and opt-in native authenticated OTLP lifecycle."""

import base64
import importlib.util
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

import bcrypt
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/jaeger"


def htpasswd(password):
    return "ods:" + bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()


def render(tmp_path, credential, bind=None, ports=("16686", "4318"), check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("JAEGER_") and key != "BIND_ADDRESS"}
    env.update(JAEGER_PORT=ports[0], JAEGER_OTLP_PORT=ports[1])
    if credential is not None:
        env["JAEGER_HTPASSWD"] = credential
    if bind is not None:
        env["BIND_ADDRESS"] = bind
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("ports", [("16686", "4318"), ("26686", "14318")])
def test_published_query_and_ingestion_bindings_share_required_auth(tmp_path, bind, ports):
    credential = htpasswd(secrets.token_hex(16))
    service = json.loads(render(tmp_path, credential, bind, ports).stdout)["services"]["jaeger"]
    assert {item["target"]: (item["host_ip"], item["published"]) for item in service["ports"]} == {
        16686: (bind or "127.0.0.1", ports[0]), 4318: (bind or "127.0.0.1", ports[1]),
    }
    assert service["environment"]["JAEGER_HTPASSWD"] == credential.replace("$", "$$")
    assert service["user"] == "10001:10001" and service["read_only"] is True
    config = yaml.safe_load((EXTENSION / "config/jaeger.yaml").read_text())
    assert config["extensions"]["jaeger_query"]["http"]["auth"] == {"authenticator": "basicauth/ods"}
    assert config["receivers"]["otlp"]["protocols"]["http"]["auth"] == {"authenticator": "basicauth/ods"}
    assert config["extensions"]["healthcheckv2"]["http"]["config"]["enabled"] is False
    storage = config["extensions"]["jaeger_storage"]["backends"]["retained"]["badger"]
    assert storage["ephemeral"] is False and storage["consistency"] is True and storage["ttl"]["spans"] == "48h"


@pytest.mark.parametrize("credential", [None, ""])
def test_missing_auth_cannot_publish_endpoints(tmp_path, credential):
    result = render(tmp_path, credential, check=False)
    assert result.returncode != 0 and "JAEGER_HTPASSWD" in result.stderr


def test_enabled_extension_uses_private_health_port(tmp_path):
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "jaeger")
    assert next(item for item in entry["env_vars"] if item["key"] == "JAEGER_HTPASSWD")["required"]
    enabled = tmp_path / "jaeger"
    enabled.mkdir()
    for name in ("compose.yaml", "manifest.yaml"):
        shutil.copy2(EXTENSION / name, enabled / name)
    spec = importlib.util.spec_from_file_location("jaeger_user_extensions", ROOT / "extensions/services/dashboard-api/user_extensions.py")
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    scanned = scanner.scan_user_extension_services(tmp_path)["jaeger"]
    assert scanned["host"] == "jaeger" and scanned["health_port"] == 13133
    assert scanned["port"] == 16686 and scanned["health"] == "/status"


@pytest.mark.skipif(os.getenv("ODS_TEST_JAEGER") != "1", reason="Opt-in real Jaeger lifecycle")
def test_native_otlp_auth_trace_relationships_rotation_and_cold_restore(tmp_path):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 10001 storage")
    password, rotated = secrets.token_hex(16), secrets.token_hex(16)
    project = "ods-q20-jaeger-" + uuid.uuid4().hex[:12]
    data = tmp_path / "data/jaeger"
    data.mkdir(parents=True)
    os.chown(data, 10001, 10001)
    shutil.copytree(EXTENSION / "config", tmp_path / "config")
    plan = json.loads(render(tmp_path, htpasswd(password)).stdout)
    service = plan["services"]["jaeger"]
    service.update(container_name=project, restart="no", labels={"io.ods.quality20.validation": "true"})
    for binding in service["ports"]:
        binding["published"] = "0"
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def http(port, path, credential=None, method="GET", payload=None, expected=200):
        binding = json.loads(run("docker", "inspect", project))[0]["NetworkSettings"]["Ports"][f"{port}/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        headers = {"Content-Type": "application/json"}
        if credential is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + credential).encode()).decode()
        request = urllib.request.Request(f"http://127.0.0.1:{binding['HostPort']}{path}", method=method, data=payload, headers=headers)
        try:
            response = opener.open(request, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {body[:200]!r}"
            if port == 16686 and expected == 401:
                assert response.headers.get("WWW-Authenticate") == 'Basic realm="ODS Jaeger"'
            return body

    trace_id = secrets.token_hex(16)
    parent_id, child_id = secrets.token_hex(8), secrets.token_hex(8)
    start = time.time_ns()
    resources = []
    for service_name, span_id, parent, offset in (("ods-fixture-api", parent_id, None, 0), ("ods-fixture-worker", child_id, parent_id, 1000000)):
        span = {"traceId": trace_id, "spanId": span_id, "name": service_name + " operation", "kind": 2,
                "startTimeUnixNano": str(start + offset), "endTimeUnixNano": str(start + offset + 2000000),
                "attributes": [{"key": "fixture.value", "value": {"stringValue": "Xin chào"}}]}
        if parent is not None:
            span["parentSpanId"] = parent
        resources.append({"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service_name}}]},
                          "scopeSpans": [{"scope": {"name": "ods-qualification"}, "spans": [span]}]})
    payload = json.dumps({"resourceSpans": resources}).encode()

    def verify_trace(credential):
        document = json.loads(http(16686, "/api/traces/" + trace_id, credential))
        assert len(document["data"]) == 1
        trace = document["data"][0]
        assert trace["traceID"] == trace_id
        spans = {item["spanID"]: item for item in trace["spans"]}
        assert set(spans) == {parent_id, child_id}
        assert spans[child_id]["references"][0]["spanID"] == parent_id
        assert {item["serviceName"] for item in trace["processes"].values()} == {"ods-fixture-api", "ods-fixture-worker"}
        assert any(item["key"] == "fixture.value" and item["value"] == "Xin chào" for item in spans[parent_id]["tags"])

    try:
        run(*command, "run", "--rm", "--no-deps", "jaeger", "validate", "--config", "/etc/jaeger/ods.yaml")
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        for bad in (None, "incorrect"):
            http(16686, "/", bad, expected=401)
            http(16686, "/api/services", bad, expected=401)
            http(4318, "/v1/traces", bad, "POST", payload, expected=401)
        assert b"<html" in http(16686, "/", password).lower()
        http(4318, "/v1/traces", password, "POST", b"{", expected=400)
        response = json.loads(http(4318, "/v1/traces", password, "POST", payload))
        assert response.get("partialSuccess", {}).get("rejectedSpans", "0") in (0, "0")
        # The collector's documented batch timeout is one second. Export
        # acceptance is not a synchronous query-visibility promise.
        time.sleep(2)
        verify_trace(password)
        result = subprocess.run(["docker", "exec", project, "wget", "-q", "-O", "-", "http://127.0.0.1:13133/config"],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode != 0 and "404" in result.stderr
        for table in ("/proc/net/tcp", "/proc/net/tcp6"):
            for line in run("docker", "exec", project, "cat", table).splitlines()[1:]:
                fields = line.split()
                if fields[3] != "0A":
                    continue
                address, hexadecimal = fields[1].rsplit(":", 1)
                port = int(hexadecimal, 16)
                assert port not in (4317, 14250, 14268, 9411, 8888)
                if port == 16685:
                    assert address == "0100007F"
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        verify_trace(password)
        service["environment"]["JAEGER_HTPASSWD"] = htpasswd(rotated).replace("$", "$$")
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        http(16686, "/api/services", password, expected=401)
        http(4318, "/v1/traces", password, "POST", payload, expected=401)
        verify_trace(rotated)
        run(*command, "stop", "--timeout", "30")
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 10001, 10001)
        next(item for item in service["volumes"] if item["target"] == "/var/lib/jaeger")["source"] = str(restored)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        verify_trace(rotated)
        http(16686, "/api/traces/" + trace_id, password, expected=401)
    finally:
        print(run(*command, "logs", "--tail", "20"))
        run(*command, "down", "--volumes")
