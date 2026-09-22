"""Metabase plan and opt-in native PostgreSQL-backed analytics lifecycle."""

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/metabase"


def render(tmp_path, password, key, bind=None, port="3032", site=None, check=True):
    env = {name: value for name, value in os.environ.items() if not name.startswith("METABASE_") and name != "BIND_ADDRESS"}
    env["METABASE_PORT"] = port
    for name, value in (("METABASE_DB_PASSWORD", password), ("METABASE_ENCRYPTION_KEY", key), ("BIND_ADDRESS", bind), ("METABASE_SITE_URL", site)):
        if value is not None:
            env[name] = value
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port", ["3032", "13032"])
def test_plan_keeps_application_database_private_and_advertises_selected_port(tmp_path, bind, port):
    password, key = secrets.token_hex(16), secrets.token_hex(32)
    plan = json.loads(render(tmp_path, password, key, bind, port).stdout)
    app, database = plan["services"]["metabase"], plan["services"]["metabase-db"]
    assert [(item["target"], item["host_ip"], item["published"]) for item in app["ports"]] == [(3000, bind or "127.0.0.1", port)]
    assert not database.get("ports") and set(database["networks"]) == {"metabase-private"}
    assert plan["networks"]["metabase-private"]["internal"] is True
    assert app["environment"]["MB_DB_PASS"] == database["environment"]["POSTGRES_PASSWORD"] == password
    assert app["environment"]["MB_ENCRYPTION_SECRET_KEY"] == key
    assert app["environment"]["MB_SITE_URL"] == "http://localhost:" + port
    assert app["user"] == "2000:2000" and database["user"] == "70:70"
    assert app["read_only"] is True and database["read_only"] is True
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "metabase")
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == {"METABASE_DB_PASSWORD", "METABASE_ENCRYPTION_KEY"}


@pytest.mark.parametrize("password,key", [(None, "valid-key"), ("", "valid-key"), ("valid-password", None), ("valid-password", "")])
def test_both_persistence_secrets_are_required(tmp_path, password, key):
    result = render(tmp_path, password, key, check=False)
    assert result.returncode != 0 and "METABASE_" in result.stderr


def test_explicit_public_site_url_takes_precedence(tmp_path):
    service = json.loads(render(tmp_path, "password", "key" * 16, port="13032", site="https://reports.example.test").stdout)["services"]["metabase"]
    assert service["environment"]["MB_SITE_URL"] == "https://reports.example.test"


@pytest.mark.skipif(os.getenv("ODS_TEST_METABASE") != "1", reason="Opt-in real Metabase lifecycle")
def test_native_setup_saved_sql_credentials_recreation_and_cold_restore(tmp_path):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 70 PostgreSQL storage")
    password, key, reader_password = secrets.token_hex(16), secrets.token_hex(32), secrets.token_hex(16)
    login_password = "Aa1!" + secrets.token_hex(16)
    project = "ods-q20-metabase-" + uuid.uuid4().hex[:12]
    data = tmp_path / "data/metabase/postgres"
    data.mkdir(parents=True)
    os.chown(data, 70, 70)
    plan = json.loads(render(tmp_path, password, key).stdout)
    for name, service in plan["services"].items():
        service.update(container_name=project + ("-db" if name.endswith("-db") else ""), restart="no",
                       labels={"io.ods.quality20.validation": "true"})
    app, database = plan["services"]["metabase"], plan["services"]["metabase-db"]
    app["ports"][0]["published"] = "0"
    plan["networks"] = {"ods-network": {"name": project + "-network"}, "metabase-private": {"name": project + "-private", "internal": True}}
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, stdin=None, check=True):
        result = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=360)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    def psql(statement, db="metabase"):
        return run("docker", "exec", "-i", project + "-db", "psql", "-X", "-v", "ON_ERROR_STOP=1", "-At", "-U", "metabase", "-d", db,
                   stdin=statement).stdout.strip()

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def http(path, session=None, method="GET", payload=None, expected=200):
        binding = json.loads(run("docker", "inspect", project).stdout)[0]["NetworkSettings"]["Ports"]["3000/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        headers = {"Content-Type": "application/json"}
        if session:
            headers["X-Metabase-Session"] = session
        request = urllib.request.Request(f"http://127.0.0.1:{binding['HostPort']}{path}", method=method,
                                         data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            response = opener.open(request, timeout=90)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {body[:300]!r}"
            if not body:
                return None
            if response.headers.get_content_type() == "application/json":
                return json.loads(body)
            return body.decode()

    def login():
        return http("/api/session", method="POST", payload={"username": "fixture@ods.invalid", "password": login_password})["id"]

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "300")
        assert http("/api/health")["status"] == "ok"
        properties = http("/api/session/properties")
        assert properties["anon-tracking-enabled"] is False
        setup_token = properties["setup-token"]
        assert setup_token
        setup = {"token": setup_token, "user": {"email": "fixture@ods.invalid", "password": login_password,
                                                "first_name": "ODS", "last_name": "Fixture"},
                 "prefs": {"site_name": "ODS qualification"}}
        session = http("/api/setup", method="POST", payload=setup)["id"]
        assert http("/api/session/properties").get("setup-token") is None
        replay = http("/api/setup", method="POST", payload=setup, expected=400)
        assert replay["errors"]["token"] == "Token does not match the setup token."
        http("/api/user/current", expected=401)
        http("/api/session", method="POST", payload={"username": "fixture@ods.invalid", "password": "incorrect"}, expected=401)
        assert http("/api/user/current", session)["email"] == "fixture@ods.invalid"
        print("Native setup, login, and consumed setup token verified", flush=True)
        psql("CREATE DATABASE ods_fixture;\nCREATE USER fixture_reader PASSWORD '" + reader_password + "';")
        psql("CREATE TABLE measurements (label text, value numeric);\nINSERT INTO measurements VALUES ('Xin chào', 42.5);\n"
             "GRANT CONNECT ON DATABASE ods_fixture TO fixture_reader;\nGRANT USAGE ON SCHEMA public TO fixture_reader;\n"
             "GRANT SELECT ON measurements TO fixture_reader;", "ods_fixture")
        source = http("/api/database", session, "POST", {
            "name": "ODS fixture warehouse", "engine": "postgres", "is_full_sync": False,
            "details": {"host": "metabase-db", "port": 5432, "dbname": "ods_fixture", "user": "fixture_reader", "password": reader_password, "ssl": False},
        })
        dataset_query = {"database": source["id"], "type": "native", "native": {"query": "SELECT label, value FROM measurements", "template-tags": {}}}
        card = http("/api/card", session, "POST", {"name": "ODS saved SQL", "dataset_query": dataset_query,
                                                "display": "table", "visualization_settings": {}, "type": "question"})
        card_id = card["id"]
        sharing = http(f"/api/card/{card_id}/public_link", session, "POST", {}, expected=400)
        assert sharing == "Public sharing is not enabled."
        assert http(f"/api/card/{card_id}", session).get("public_uuid") is None

        def query_card(current_session):
            result = http(f"/api/card/{card_id}/query", current_session, "POST", {}, expected=202)
            assert result["status"] == "completed", result
            assert result["data"]["rows"] == [["Xin chào", 42.5]]

        query_card(session)
        details = psql("SELECT details::text FROM metabase_database WHERE id = " + str(source["id"]) + ";")
        assert details and reader_password not in details and "fixture_reader" not in details
        forbidden = {**dataset_query, "native": {"query": "UPDATE measurements SET value = 0", "template-tags": {}}}
        denied = http("/api/dataset", session, "POST", forbidden, expected=400)
        assert denied["status"] == "failed" and "permission denied" in denied["error"].lower()
        query_card(session)
        print("Saved SQL, encrypted connection details, and source read-only grant verified", flush=True)
        http("/api/session", session, "DELETE", {"session_id": session}, expected=204)
        http("/api/user/current", session, expected=401)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "300")
        session = login()
        assert http("/api/session/properties").get("setup-token") is None
        query_card(session)
        assert not json.loads(run("docker", "inspect", project + "-db").stdout)[0]["NetworkSettings"]["Ports"].get("5432/tcp")
        print("Recreation preserved login, saved SQL, and private database", flush=True)
        run(*command, "stop", "--timeout", "45")
        restored = tmp_path / "restored-postgres"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 70, 70)
        database["volumes"][0]["source"] = str(restored)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "300")
        session = login()
        query_card(session)
        print("Cold PostgreSQL restore preserved saved SQL", flush=True)
        app["environment"]["MB_ENCRYPTION_SECRET_KEY"] = secrets.token_hex(32)
        config.write_text(json.dumps(plan))
        mismatch = run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120", "metabase", check=False)
        assert mismatch.returncode != 0
        rejected_logs = run(*command, "logs", "--tail", "100", "metabase").stdout.lower()
        assert "database was encrypted with a different key than the mb_encryption_secret_key environment contains" in rejected_logs
        app["environment"]["MB_ENCRYPTION_SECRET_KEY"] = key
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "300", "metabase")
        query_card(login())
    finally:
        print(run(*command, "logs", "--tail", "20").stdout)
        run(*command, "down", "--volumes")
