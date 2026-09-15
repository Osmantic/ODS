"""NocoDB rendered contract and opt-in native table/credential lifecycle."""

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
EXTENSION = ROOT / "extensions/library/services/nocodb"
REQUIRED = ("NOCODB_ADMIN_PASSWORD", "NOCODB_DB_PASSWORD", "NOCODB_JWT_SECRET", "NOCODB_CONNECTION_KEY")


def render(tmp_path, values, bind=None, port="8087", site=None, check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("NOCODB_") and key != "BIND_ADDRESS"}
    env.update(values, NOCODB_PORT=port)
    if bind is not None:
        env["BIND_ADDRESS"] = bind
    if site is not None:
        env["NOCODB_SITE_URL"] = site
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port", ["8087", "18087"])
def test_plan_keeps_database_private_and_advertises_selected_port(tmp_path, bind, port):
    values = {key: "Aa1!" + secrets.token_hex(16) for key in REQUIRED}
    plan = json.loads(render(tmp_path, values, bind, port).stdout)
    edge, app, database = (plan["services"][name] for name in ("nocodb", "nocodb-app", "nocodb-db"))
    assert [(item["target"], item["host_ip"], item["published"]) for item in edge["ports"]] == [(8080, bind or "127.0.0.1", port)]
    assert not database.get("ports") and set(database["networks"]) == {"nocodb-private"}
    assert plan["networks"]["nocodb-private"]["internal"] is True
    assert app["environment"]["NOCODB_DB_PASSWORD"] == database["environment"]["POSTGRES_PASSWORD"] == values["NOCODB_DB_PASSWORD"]
    assert app["environment"]["NC_ADMIN_EMAIL"] == "admin@ods.local"
    assert app["environment"]["NC_SITE_URL"] == "http://localhost:" + port
    assert app["user"] == "1000:1000" and database["user"] == "70:70"
    assert app["read_only"] is True and database["read_only"] is True
    assert not app.get("ports") and set(app["networks"]) == {"nocodb-private"}
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "nocodb")
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == set(REQUIRED)


@pytest.mark.parametrize("missing", REQUIRED)
@pytest.mark.parametrize("empty", [False, True])
def test_all_four_state_secrets_are_required(tmp_path, missing, empty):
    values = {key: secrets.token_hex(16) for key in REQUIRED if key != missing}
    if empty:
        values[missing] = ""
    result = render(tmp_path, values, check=False)
    assert result.returncode != 0 and missing in result.stderr


def test_explicit_site_url_takes_precedence(tmp_path):
    values = {key: secrets.token_hex(16) for key in REQUIRED}
    app = json.loads(render(tmp_path, values, port="18087", site="https://tables.example.test").stdout)["services"]["nocodb-app"]
    assert app["environment"]["NC_SITE_URL"] == "https://tables.example.test"


@pytest.mark.skipif(os.getenv("ODS_TEST_NOCODB") != "1", reason="Opt-in native NocoDB lifecycle")
def test_native_tables_tokens_signup_recreation_and_cold_restore(tmp_path):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 70 PostgreSQL storage")
    values = {key: "Aa1!" + secrets.token_hex(16) for key in REQUIRED}
    values["NOCODB_DB_PASSWORD"] += "'@/:?&=+%"
    project = "ods-q20-nocodb-" + uuid.uuid4().hex[:12]
    data = tmp_path / "data/nocodb"
    for name, uid in (("files", 1000), ("postgres", 70)):
        directory = data / name
        directory.mkdir(parents=True)
        os.chown(directory, uid, uid)
    plan = json.loads(render(tmp_path, values).stdout)
    for name, service in plan["services"].items():
        service.update(container_name=project + name.removeprefix("nocodb"), restart="no",
                       labels={"io.ods.quality20.validation": "true"})
    edge, app, database = (plan["services"][name] for name in ("nocodb", "nocodb-app", "nocodb-db"))
    edge["ports"][0]["published"] = "0"
    edge["volumes"][0]["source"] = str(EXTENSION / "nginx.conf")
    app["volumes"][0]["source"] = str(EXTENSION / "configure.mjs")
    plan["networks"] = {"ods-network": {"name": project + "-network"}, "nocodb-private": {"name": project + "-private", "internal": True}}
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, stdin=None):
        result = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def http(path, token=None, method="GET", payload=None, expected=200, api=False):
        binding = json.loads(run("docker", "inspect", project).stdout)[0]["NetworkSettings"]["Ports"]["8080/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["xc-token" if api else "xc-auth"] = token
        request = urllib.request.Request(f"http://127.0.0.1:{binding['HostPort']}{path}", method=method,
                                         data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            response = opener.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {body[:300]!r}"
            if not body:
                return None
            return json.loads(body) if response.headers.get_content_type() == "application/json" else body.decode()

    def login(password):
        return http("/api/v2/auth/user/signin", method="POST", payload={"email": "admin@ods.local", "password": password})["token"]

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "240")
        http("/api/v1/health")
        sockets = run("docker", "exec", project, "cat", "/proc/net/tcp", "/proc/net/tcp6").stdout
        listeners = {int(line.split()[1].split(":")[1], 16) for line in sockets.splitlines()
                     if len(line.split()) > 3 and line.split()[3] == "0A"}
        assert 8080 in listeners and 5433 not in listeners
        private = json.loads(run("docker", "inspect", project + "-app").stdout)[0]
        assert set(private["NetworkSettings"]["Networks"]) == {project + "-private"}
        assert not any(private["NetworkSettings"]["Ports"].values())
        http("/api/v2/meta/bases", expected=401)
        http("/api/v2/auth/user/signin", method="POST", payload={"email": "admin@ods.local", "password": "incorrect"}, expected=400)
        session = login(values["NOCODB_ADMIN_PASSWORD"])
        info = http("/api/v1/db/meta/nocodb/info")
        assert info["teleEnabled"] is False and info["errorReportingEnabled"] is False and info["feedEnabled"] is False
        settings = http("/api/v1/app-settings", session)
        assert settings["invite_only_signup"] is False
        http("/api/v1/app-settings", session, "POST", {**settings, "invite_only_signup": True})
        assert http("/api/v1/app-settings", session)["invite_only_signup"] is True
        http("/api/v2/auth/user/signup", method="POST", payload={"email": "uninvited@ods.invalid", "password": "Aa1!" + secrets.token_hex(16)}, expected=400)
        base = http("/api/v2/meta/bases", session, "POST", {"title": "ODS qualification"})
        base_id = base["id"]
        table = http(f"/api/v2/meta/bases/{base_id}/tables", session, "POST", {
            "table_name": "observations", "title": "Observations", "columns": [
                {"column_name": "id", "title": "Id", "uidt": "ID", "dt": "int4", "pk": True, "ai": True, "rqd": True},
                {"column_name": "label", "title": "Label", "uidt": "SingleLineText", "pv": True},
                {"column_name": "score", "title": "Score", "uidt": "Decimal"},
            ],
        })
        table_id = table["id"]
        route = f"/api/v2/tables/{table_id}/records"
        http(route, expected=401)
        row = http(route, session, "POST", {"Label": "Xin chào", "Score": 42.5})
        row_id = row["Id"]
        api_token = http(f"/api/v2/meta/bases/{base_id}/api-tokens", session, "POST", {"description": "ODS workflow"})
        token = api_token["token"]

        def records():
            rows = http(route, token, api=True)["list"]
            assert [(item["Label"], float(item["Score"])) for item in rows] == [("Xin chào", 43.5)]

        http(route, token, "PATCH", {"Id": row_id, "Score": 43.5}, api=True)
        records()
        other = http("/api/v2/meta/bases", session, "POST", {"title": "Separate base"})
        denied = http(f"/api/v2/meta/bases/{other['id']}/tables", token, expected=401, api=True)
        assert denied["error"] == "ERR_AUTHENTICATION_REQUIRED"
        print("Native typed CRUD, base-scoped token, signup control and privacy flags verified", flush=True)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "240")
        records()
        session = login(values["NOCODB_ADMIN_PASSWORD"])
        assert http("/api/v1/app-settings", session)["invite_only_signup"] is True
        rotated = "Aa1!" + secrets.token_hex(16)
        app["environment"]["NC_ADMIN_PASSWORD"] = rotated
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "240", "nocodb-app", "nocodb")
        http("/api/v2/auth/user/signin", method="POST", payload={"email": "admin@ods.local", "password": values["NOCODB_ADMIN_PASSWORD"]}, expected=400)
        http("/api/v1/app-settings", session, expected=401)
        session = login(rotated)
        records()
        assert not json.loads(run("docker", "inspect", project + "-db").stdout)[0]["NetworkSettings"]["Ports"].get("5432/tcp")
        print("Recreation, managed administrator rotation and persisted rows verified", flush=True)
        run(*command, "stop", "--timeout", "45")
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for name, uid in (("files", 1000), ("postgres", 70)):
            for path in [restored / name, *(restored / name).rglob("*")]:
                os.chown(path, uid, uid)
        app["volumes"][1]["source"] = str(restored / "files")
        database["volumes"][0]["source"] = str(restored / "postgres")
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "240")
        session = login(rotated)
        assert http("/api/v1/app-settings", session)["invite_only_signup"] is True
        records()
        http(f"/api/v2/meta/bases/{base_id}/api-tokens/{api_token['id']}", session, "DELETE")
        http(route, token, expected=401, api=True)
        http(route, session, "DELETE", {"Id": row_id})
        assert http(route, session)["list"] == []
        print("Cold restore, workflow token revocation and row deletion verified", flush=True)
    finally:
        print(run(*command, "logs", "--tail", "15").stdout)
        run(*command, "down", "--volumes")
