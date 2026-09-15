"""Rendered QuestDB contract and opt-in native HTTP/PG lifecycle."""

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
EXTENSION = ROOT / "extensions/library/services/questdb"


def render(tmp_path, password, bind=None, ports=("9100", "8812"), check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("QUESTDB_") and key != "BIND_ADDRESS"}
    env.update(QUESTDB_PORT=ports[0], QUESTDB_PG_PORT=ports[1])
    if password is not None:
        env["QUESTDB_PASSWORD"] = password
    if bind is not None:
        env["BIND_ADDRESS"] = bind
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("ports", [("9100", "8812"), ("19100", "18812")])
def test_plan_preserves_authenticated_listeners_and_operator_bindings(tmp_path, bind, ports):
    password = secrets.token_hex(24)
    service = json.loads(render(tmp_path, password, bind, ports).stdout)["services"]["questdb"]
    published = {item["target"]: (item["host_ip"], item["published"]) for item in service["ports"]}
    assert published == {9000: (bind or "127.0.0.1", ports[0]), 8812: (bind or "127.0.0.1", ports[1])}
    env = service["environment"]
    assert env["QDB_HTTP_PASSWORD"] == env["QDB_PG_PASSWORD"] == password
    assert env["QDB_HTTP_USER"] == env["QDB_PG_USER"] == "ods"
    assert all(env[key] == "false" for key in ("QDB_LINE_TCP_ENABLED", "QDB_LINE_UDP_ENABLED", "QDB_QWP_UDP_ENABLED", "QDB_PG_READONLY_USER_ENABLED"))
    assert env["QDB_HTTP_MIN_NET_BIND_TO"] == "127.0.0.1:9003"
    assert service["user"] == "10001:10001" and service["read_only"] is True
    assert service["volumes"][0]["source"] == str(tmp_path / "data/questdb")
    assert service["volumes"][0]["target"] == "/var/lib/questdb"
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "questdb")
    assert next(item for item in entry["env_vars"] if item["key"] == "QUESTDB_PASSWORD")["required"]


@pytest.mark.parametrize("password", [None, ""])
def test_missing_password_cannot_publish_default_accounts(tmp_path, password):
    result = render(tmp_path, password, check=False)
    assert result.returncode != 0 and "QUESTDB_PASSWORD" in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_QUESTDB") != "1", reason="Opt-in real QuestDB lifecycle")
def test_http_and_pg_ingestion_auth_recreation_rotation_and_cold_restore(tmp_path):
    import psycopg

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 10001 storage")
    password, rotated = secrets.token_hex(24), secrets.token_hex(24)
    project = "ods-q20-questdb-" + uuid.uuid4().hex[:12]
    data = tmp_path / "data/questdb"
    data.mkdir(parents=True)
    os.chown(data, 10001, 10001)
    plan = json.loads(render(tmp_path, password).stdout)
    service = plan["services"]["questdb"]
    service.update(container_name=project, restart="no", labels={"io.ods.quality20.validation": "true"})
    for binding in service["ports"]:
        binding["published"] = "0"
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=150)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def port(number):
        binding = json.loads(run("docker", "inspect", project))[0]["NetworkSettings"]["Ports"][f"{number}/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return int(binding["HostPort"])

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def http(path, credential=None, method="GET", payload=None, expected=200):
        headers = {"Content-Type": "text/plain"}
        if credential is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + credential).encode()).decode()
        request = urllib.request.Request(f"http://127.0.0.1:{port(9000)}{path}", method=method, data=payload, headers=headers)
        try:
            response = opener.open(request, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {body[:300]!r}"
            return body

    def sql(query, credential):
        return json.loads(http("/exec?" + urllib.parse.urlencode({"query": query}), credential))

    def connect(credential, user="ods"):
        return psycopg.connect(host="127.0.0.1", port=port(8812), user=user, password=credential,
                               dbname="qdb", connect_timeout=5, autocommit=True, sslmode="disable")

    payload = b'ods_fixture,workflow=sample duration_ms=42.5,status="ok"\n'
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "90")
        assert b"<html" in http("/index.html").lower()
        for bad in (None, "incorrect"):
            http("/exec?query=select%201", bad, expected=401)
            http("/write?precision=n", bad, "POST", payload, expected=401)
        for user, credential in (("ods", "incorrect"), ("admin", "quest"), ("user", "quest")):
            with pytest.raises(psycopg.OperationalError):
                connect(credential, user)
        http("/write?precision=n", password, "POST", payload, expected=204)
        assert sql("SELECT wait_wal_table('ods_fixture')", password)["dataset"] == [[True]]
        query = "SELECT workflow, duration_ms, status FROM ods_fixture"
        assert sql(query, password)["dataset"] == [["sample", 42.5, "ok"]]
        with connect(password) as connection:
            assert connection.execute(query).fetchall() == [("sample", 42.5, "ok")]
        parameters = sql("SHOW PARAMETERS", password)
        keys = [item["name"] for item in parameters["columns"]]
        configured = {row[keys.index("property_path")]: row[keys.index("value")] for row in parameters["dataset"]}
        for key in ("line.tcp.enabled", "line.udp.enabled", "qwp.udp.enabled", "pg.readonly.user.enabled", "telemetry.enabled"):
            assert configured[key] == "false"
        assert configured["http.min.net.bind.to"] == "127.0.0.1:9003"
        for table in ("/proc/net/tcp", "/proc/net/tcp6"):
            for line in run("docker", "exec", project, "cat", table).splitlines()[1:]:
                fields = line.split()
                if fields[3] != "0A":
                    continue
                address, hexadecimal = fields[1].rsplit(":", 1)
                listening = int(hexadecimal, 16)
                assert listening != 9009
                if listening == 9003:
                    assert address == "0100007F"
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        assert sql(query, password)["dataset"] == [["sample", 42.5, "ok"]]
        service["environment"].update(QDB_HTTP_PASSWORD=rotated, QDB_PG_PASSWORD=rotated)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        http("/exec?query=select%201", password, expected=401)
        with pytest.raises(psycopg.OperationalError):
            connect(password)
        with connect(rotated) as connection:
            assert connection.execute(query).fetchall() == [("sample", 42.5, "ok")]
        run(*command, "stop", "--timeout", "30")
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 10001, 10001)
        service["volumes"][0]["source"] = str(restored)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90")
        assert sql(query, rotated)["dataset"] == [["sample", 42.5, "ok"]]
        sql("DROP TABLE ods_fixture", rotated)
        assert json.loads(run("docker", "inspect", project))[0]["State"]["Health"]["Status"] == "healthy"
    finally:
        print(run(*command, "logs", "--tail", "25"))
        run(*command, "down", "--volumes")
