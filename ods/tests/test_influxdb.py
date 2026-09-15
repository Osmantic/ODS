"""Rendered extension plan and opt-in native InfluxDB v2 lifecycle."""

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

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/influxdb"


def render(tmp_path, password, token, bind=None, port="8086", check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("INFLUXDB_") and key != "BIND_ADDRESS"}
    env["INFLUXDB_PORT"] = port
    for key, value in (("INFLUXDB_PASSWORD", password), ("INFLUXDB_ADMIN_TOKEN", token), ("BIND_ADDRESS", bind)):
        if value is not None:
            env[key] = value
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port", ["8086", "18086"])
def test_plan_preserves_native_setup_storage_and_operator_bindings(tmp_path, bind, port):
    password, token = secrets.token_hex(16), secrets.token_hex(32)
    service = json.loads(render(tmp_path, password, token, bind, port).stdout)["services"]["influxdb"]
    assert [(item["target"], item["host_ip"], item["published"]) for item in service["ports"]] == [(8086, bind or "127.0.0.1", port)]
    env = service["environment"]
    assert env["DOCKER_INFLUXDB_INIT_PASSWORD"] == password
    assert env["DOCKER_INFLUXDB_INIT_ADMIN_TOKEN"] == token
    assert env["DOCKER_INFLUXDB_INIT_RETENTION"] == "168h"
    assert service["user"] == "1000:1000" and service["read_only"] is True
    assert {item["target"] for item in service["volumes"]} == {"/var/lib/influxdb2", "/etc/influxdb2"}
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "influxdb")
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == {"INFLUXDB_PASSWORD", "INFLUXDB_ADMIN_TOKEN"}


@pytest.mark.parametrize("password,token", [(None, "valid-token"), ("", "valid-token"), ("valid-password", None), ("valid-password", "")])
def test_initial_secrets_required_before_publishing(tmp_path, password, token):
    result = render(tmp_path, password, token, check=False)
    assert result.returncode != 0 and "INFLUXDB_" in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_INFLUXDB") != "1", reason="Opt-in real InfluxDB lifecycle")
def test_native_scoped_tokens_flux_revocation_seed_precedence_and_cold_restore(tmp_path):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 1000 storage")
    password, operator = secrets.token_hex(16), secrets.token_hex(32)
    project = "ods-q20-influxdb-" + uuid.uuid4().hex[:12]
    data = tmp_path / "data/influxdb"
    for name in ("storage", "config"):
        directory = data / name
        directory.mkdir(parents=True)
        os.chown(directory, 1000, 1000)
    plan = json.loads(render(tmp_path, password, operator).stdout)
    service = plan["services"]["influxdb"]
    service.update(container_name=project, restart="no", labels={"io.ods.quality20.validation": "true"})
    service["ports"][0]["published"] = "0"
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

    def http(path, token=None, method="GET", body=None, expected=200, content_type="application/json", login=None):
        binding = json.loads(run("docker", "inspect", project))[0]["NetworkSettings"]["Ports"]["8086/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        headers = {"Content-Type": content_type, "Accept": "application/json"}
        if token is not None:
            headers["Authorization"] = "Token " + token
        if login is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + login).encode()).decode()
        request = urllib.request.Request(f"http://127.0.0.1:{binding['HostPort']}{path}", method=method, data=body, headers=headers)
        try:
            response = opener.open(request, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            payload = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {payload[:200]!r}"
            return payload

    def api(path, token=operator, method="GET", body=None, expected=200):
        return json.loads(http(path, token, method, json.dumps(body).encode() if body is not None else None, expected))

    query = 'from(bucket: "workflows") |> range(start: -1h) |> filter(fn: (r) => r._measurement == "ods_fixture")'

    def flux(token, expected=200):
        return http("/api/v2/query?org=ods", token, "POST", query.encode(), expected, "application/vnd.flux")

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        assert api("/api/v2/setup", None) == {"allowed": False}
        assert api("/health", None)["status"] == "pass"
        http("/api/v2/signin", method="POST", login=password, expected=204)
        http("/api/v2/signin", method="POST", login="incorrect", expected=401)
        for bad in (None, "incorrect"):
            http("/api/v2/buckets", bad, expected=401)
            flux(bad, expected=401)
        assert api("/metrics", None, expected=403)["message"] == "metrics disabled"
        http("/debug/pprof/", expected=403)
        bucket = api("/api/v2/buckets?name=workflows")["buckets"][0]
        assert bucket["retentionRules"][0]["everySeconds"] == 604800
        org_id, bucket_id = bucket["orgID"], bucket["id"]

        def scoped(action):
            return api("/api/v2/authorizations", method="POST", expected=201, body={
                "orgID": org_id, "description": "ODS fixture " + action,
                "permissions": [{"action": action, "resource": {"type": "buckets", "id": bucket_id, "orgID": org_id}}],
            })

        writer, reader = scoped("write"), scoped("read")
        write_path = "/api/v2/write?" + urllib.parse.urlencode({"org": "ods", "bucket": "workflows", "precision": "s"})
        payload = b"ods_fixture,workflow=sample duration_ms=42.5\n"
        http(write_path, reader["token"], "POST", payload, 403, "text/plain")
        http(write_path, writer["token"], "POST", payload, 204, "text/plain")
        # Flux hides buckets the token cannot read rather than disclosing them.
        flux(writer["token"], expected=404)
        assert b"42.5" in flux(reader["token"])
        escalation = {"orgID": org_id, "permissions": [{"action": "read", "resource": {"type": "buckets", "id": bucket_id, "orgID": org_id}}]}
        http("/api/v2/authorizations", writer["token"], "POST", json.dumps(escalation).encode(), expected=401)
        http("/api/v2/authorizations/" + writer["id"], operator, "DELETE", expected=204)
        http(write_path, writer["token"], "POST", payload, 401, "text/plain")
        service["environment"].update(DOCKER_INFLUXDB_INIT_PASSWORD=secrets.token_hex(16),
                                       DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=secrets.token_hex(32))
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        assert b"42.5" in flux(reader["token"])
        http("/api/v2/signin", method="POST", login=password, expected=204)
        http("/api/v2/signin", method="POST", login=service["environment"]["DOCKER_INFLUXDB_INIT_PASSWORD"], expected=401)
        http("/api/v2/buckets", service["environment"]["DOCKER_INFLUXDB_INIT_ADMIN_TOKEN"], expected=401)
        assert api("/api/v2/buckets?name=workflows")["buckets"][0]["id"] == bucket_id
        http(write_path, writer["token"], "POST", payload, 401, "text/plain")
        run(*command, "stop", "--timeout", "30")
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 1000, 1000)
        for volume in service["volumes"]:
            volume["source"] = str(restored / Path(volume["source"]).name)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        assert b"42.5" in flux(reader["token"])
        http(write_path, writer["token"], "POST", payload, 401, "text/plain")
        http("/api/v2/authorizations/" + reader["id"], operator, "DELETE", expected=204)
        flux(reader["token"], expected=401)
        assert api("/health", None)["status"] == "pass"
    finally:
        print(run(*command, "logs", "--tail", "20"))
        run(*command, "down", "--volumes")
