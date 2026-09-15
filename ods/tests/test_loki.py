"""Native LogQL authentication, tenant mapping and single-node cold recovery."""

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

import bcrypt
import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/loki"
REQUIRED = ("LOKI_WRITER_HASH", "LOKI_READER_HASH")


def render(tmp_path, values, bind=None, port="3100", check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("LOKI_") and key != "BIND_ADDRESS"}
    env.update(values, LOKI_PORT=port)
    if bind is not None:
        env["BIND_ADDRESS"] = bind
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port", ["3100", "13100"])
def test_plan_exposes_only_gateway_and_isolates_store(tmp_path, bind, port):
    plan = json.loads(render(tmp_path, dict.fromkeys(REQUIRED, "hash"), bind, port).stdout)
    gateway, store = plan["services"]["loki"], plan["services"]["loki-store"]
    assert [(item["target"], item["host_ip"], item["published"]) for item in gateway["ports"]] == [(8080, bind or "127.0.0.1", port)]
    assert not store.get("ports") and set(store["networks"]) == {"loki-private"}
    assert plan["networks"]["loki-private"]["internal"] is True
    assert store["user"] == "10001:10001" and store["read_only"] is True
    assert gateway["user"] == "101:101" and gateway["read_only"] is True
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "loki")
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == set(REQUIRED)


@pytest.mark.parametrize("missing", REQUIRED)
@pytest.mark.parametrize("empty", [False, True])
def test_gateway_credentials_are_required(tmp_path, missing, empty):
    values = {key: "hash" for key in REQUIRED if key != missing}
    if empty:
        values[missing] = ""
    result = render(tmp_path, values, check=False)
    assert result.returncode != 0 and missing in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_LOKI") != "1", reason="Opt-in real Loki lifecycle")
def test_native_logql_roles_tenant_mapping_rotation_and_restore(tmp_path):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 10001 storage")
    passwords = {key: secrets.token_urlsafe(24) for key in ("writer", "reader")}

    def password_hash(value):
        return bcrypt.hashpw(value.encode(), bcrypt.gensalt(rounds=8)).decode()

    values = {"LOKI_" + key.upper() + "_HASH": password_hash(value) for key, value in passwords.items()}
    project = "ods-q20-loki-" + uuid.uuid4().hex[:12]
    data = tmp_path / "data/loki"
    data.mkdir(parents=True)
    os.chown(data, 10001, 10001)
    plan = json.loads(render(tmp_path, values).stdout)
    for name, service in plan["services"].items():
        service.update(container_name=project + "-" + name, restart="no", labels={"io.ods.quality20.validation": "true"})
        for mount in service["volumes"]:
            if mount.get("read_only"):
                mount["source"] = str(EXTENSION / Path(mount["source"]).name)
    gateway = plan["services"]["loki"]
    store = plan["services"]["loki-store"]
    gateway["ports"][0]["published"] = "0"
    plan["networks"] = {"ods-network": {"name": project + "-public"}, "loki-private": {"name": project + "-private", "internal": True}}
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

    def http(path, role=None, method="GET", payload=None, expected=200, headers=None, direct=False, password=None):
        name, port = ("loki-store", "3100/tcp") if direct else ("loki", "8080/tcp")
        inspected = json.loads(run("docker", "inspect", project + "-" + name).stdout)[0]
        if direct:
            # The local Docker host can inspect its private bridge. No backend
            # port is published and other ODS containers do not join it.
            assert set(inspected["NetworkSettings"]["Networks"]) == {project + "-private"}
            address = inspected["NetworkSettings"]["Networks"][project + "-private"]["IPAddress"] + ":3100"
        else:
            binding = inspected["NetworkSettings"]["Ports"][port][0]
            assert binding["HostIp"] == "127.0.0.1"
            address = "127.0.0.1:" + binding["HostPort"]
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        if role:
            request_headers["Authorization"] = "Basic " + base64.b64encode((role + ":" + (password or passwords[role])).encode()).decode()
        request = urllib.request.Request("http://" + address + path, method=method,
                                         data=json.dumps(payload).encode() if payload is not None else None, headers=request_headers)
        try:
            response = opener.open(request, timeout=40)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {body[:400]!r}"
            if expected == 401 and not direct:
                assert response.headers["WWW-Authenticate"] == 'Basic realm="ODS Loki"'
            return json.loads(body) if body and response.headers.get_content_type() == "application/json" else body.decode()

    timestamp = time.time_ns()
    message = "Xin chào ODS log: artifact=42"
    query = "/loki/api/v1/query_range?" + urllib.parse.urlencode({"query": '{job="ods-test"} |= "artifact"', "start": str(timestamp - 1000000000), "end": str(timestamp + 1000000000)})
    payload = {"streams": [{"stream": {"job": "ods-test"}, "values": [[str(timestamp), message]]}]}

    def verify():
        result = http(query, "reader", headers={"X-Scope-OrgID": "other|ods"})
        assert result["status"] == "success" and result["data"]["resultType"] == "streams"
        assert [value[1] for stream in result["data"]["result"] for value in stream["values"]] == [message]

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        http("/ready")
        for role in (None, "reader"):
            http("/loki/api/v1/push", role, "POST", payload, expected=401)
        http(query, "writer", expected=401)
        http(query, "reader", password="incorrect", expected=401)
        http("/loki/api/v1/push", "writer", "POST", payload, expected=204, headers={"X-Scope-OrgID": "forged"})
        other = {"streams": [{"stream": {"job": "ods-test"}, "values": [[str(timestamp), "private artifact from other tenant"]]}]}
        http("/loki/api/v1/push", method="POST", payload=other, expected=204, headers={"X-Scope-OrgID": "other"}, direct=True)
        verify()
        assert "job" in http("/loki/api/v1/labels", "reader")["data"]
        assert "ods-test" in http("/loki/api/v1/label/job/values", "reader")["data"]
        metric = "/loki/api/v1/query?" + urllib.parse.urlencode({"query": 'sum(count_over_time({job="ods-test"}[5m]))', "time": str(timestamp + 1000000000)})
        assert http(metric, "reader")["data"]["result"][0]["value"][1] == "1"
        http("/loki/api/v1/push", "writer", "POST", {"streams": [{"stream": {"job": "ods-test"}, "values": [[str(timestamp - 8 * 86400 * 1000000000), "old"]]}]}, expected=400)
        http("/loki/api/v1/push", "writer", "POST", {"streams": [{"stream": {"job": "ods-test"}, "values": [[str(timestamp + 1), "x" * 65537]]}]}, expected=400)
        http("/loki/api/v1/query_range", "reader", "POST", {}, expected=403)
        for path in ("/config", "/metrics", "/flush", "/loki/api/v1/delete", "/services", "/debug/pprof/"):
            http(path, "writer", expected=404)
        print("Native LogQL, label APIs, metric query, denied writer/reader cross-use and forced tenant mapping verified", flush=True)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        verify()
        old_password = passwords["reader"]
        passwords["reader"] = secrets.token_urlsafe(24)
        gateway["environment"]["LOKI_READER_HASH"] = password_hash(passwords["reader"]).replace("$", "$$")
        save()
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120", "loki")
        http(query, "reader", password=old_password, expected=401)
        verify()
        assert run("docker", "exec", project + "-loki", "stat", "-c", "%a:%u:%g", "/tmp/readers").stdout.strip() == "600:101:101"
        run(*command, "stop", "--timeout", "60")
        assert list((data / "chunks").rglob("*")) and (data / "wal").is_dir()
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 10001, 10001)
        store["volumes"][0]["source"] = str(restored)
        save()
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        verify()
        print("Graceful recreation, credential rotation and complete cold TSDB/WAL/chunk restore verified", flush=True)
        gateway["environment"]["LOKI_READER_HASH"] += "\nwriter:injected"
        save()
        invalid = run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "45", "loki", check=False)
        assert invalid.returncode != 0
        assert "requires a single bcrypt hash" in run(*command, "logs", "--tail", "20", "loki").stdout
        gateway["environment"]["LOKI_READER_HASH"] = password_hash(passwords["reader"]).replace("$", "$$")
        save()
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120", "loki")
        verify()
        assert "error loading cache generation numbers" not in run(*command, "logs", "loki-store").stdout
    finally:
        print(run(*command, "logs", "--tail", "12").stdout)
        run(*command, "down", "--volumes")
