"""Native REST transactions, JWT/database grants and cold PostgreSQL recovery."""

import base64
import hashlib
import hmac
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

import psycopg
from psycopg import sql
import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/postgrest"
REQUIRED = ("POSTGREST_DB_PASSWORD", "POSTGREST_POSTGRES_PASSWORD", "POSTGREST_JWT_SECRET")


def render(tmp_path, values, bind=None, port="3005", check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("POSTGREST_") and key != "BIND_ADDRESS"}
    env.update(values, POSTGREST_PORT=port)
    if bind is not None:
        env["BIND_ADDRESS"] = bind
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port", ["3005", "13005"])
def test_plan_publishes_only_authenticated_rest(tmp_path, bind, port):
    plan = json.loads(render(tmp_path, dict.fromkeys(REQUIRED, "configured"), bind, port).stdout)
    api, database = plan["services"]["postgrest"], plan["services"]["postgrest-db"]
    assert [(item["target"], item["host_ip"], item["published"]) for item in api["ports"]] == [(3000, bind or "127.0.0.1", port)]
    assert not database.get("ports") and set(database["networks"]) == {"postgrest-private"}
    assert plan["networks"]["postgrest-private"]["internal"] is True
    assert api["user"] == "1000:1000" and api["read_only"] is True
    assert "PGRST_DB_ANON_ROLE" not in api["environment"]
    assert "POSTGRES_PASSWORD" not in api["environment"]
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "postgrest")
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == set(REQUIRED)


@pytest.mark.parametrize("missing", REQUIRED)
@pytest.mark.parametrize("empty", [False, True])
def test_all_native_credentials_are_required(tmp_path, missing, empty):
    values = {key: "configured" for key in REQUIRED if key != missing}
    if empty:
        values[missing] = ""
    result = render(tmp_path, values, check=False)
    assert result.returncode != 0 and missing in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_POSTGREST") != "1", reason="Opt-in real PostgREST and PostgreSQL")
def test_native_jwt_grants_atomic_batches_rotation_and_cold_restore(tmp_path):
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated PostgreSQL UID 70 storage")
    values = {key: secrets.token_urlsafe(32) for key in REQUIRED}
    for key in ("POSTGREST_DB_PASSWORD", "POSTGREST_POSTGRES_PASSWORD"):
        values[key] += "'\\$ @:;[]"
    project = "ods-q20-postgrest-" + uuid.uuid4().hex[:12]
    data = tmp_path / "data/postgrest"
    data.mkdir(parents=True)
    os.chown(data, 70, 70)
    plan = json.loads(render(tmp_path, values).stdout)
    for name, service in plan["services"].items():
        service.update(container_name=project + "-" + name, restart="no", labels={"io.ods.quality20.validation": "true"})
    api, database = plan["services"]["postgrest"], plan["services"]["postgrest-db"]
    api["ports"][0]["published"] = "0"
    database["volumes"][1]["source"] = str(EXTENSION / "init.sql")
    plan["networks"] = {"ods-network": {"name": project + "-public"}, "postgrest-private": {"name": project + "-private", "internal": True}}
    # Test-only client supplies a real readiness probe for the scratch API image.
    # The product dashboard uses the same native admin /ready endpoint directly.
    plan["services"]["postgrest-probe"] = {
        "image": database["image"], "container_name": project + "-probe", "entrypoint": ["sleep", "infinity"],
        "user": "70:70", "read_only": True, "networks": {"ods-network": {}},
        "healthcheck": {"test": ["CMD", "wget", "-q", "--spider", "http://postgrest:3001/ready"], "interval": "5s", "timeout": "3s", "retries": 12, "start_period": "10s"},
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

    def token(role="ods_reader", signing_key=None, audience="ods-postgrest", expires=None):
        def encoded(value):
            return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=")
        payload = {"role": role, "aud": audience, "exp": int(time.time()) + 3600 if expires is None else expires}
        content = encoded({"alg": "HS256", "typ": "JWT"}) + b"." + encoded(payload)
        signature = hmac.new((signing_key or values["POSTGREST_JWT_SECRET"]).encode(), content, hashlib.sha256).digest()
        return (content + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def http(path, jwt=None, method="GET", payload=None, expected=200, headers=None):
        binding = json.loads(run("docker", "inspect", project + "-postgrest").stdout)[0]["NetworkSettings"]["Ports"]["3000/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        if jwt:
            request_headers["Authorization"] = "Bearer " + jwt
        request = urllib.request.Request("http://127.0.0.1:" + binding["HostPort"] + path, method=method,
                                         data=json.dumps(payload).encode() if payload is not None else None, headers=request_headers)
        try:
            response = opener.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {body[:400]!r}"
            return (json.loads(body) if body else None), dict(response.headers)

    first = {"id": str(uuid.uuid4()), "title": "Xin chào ODS", "metadata": {"artifact": "local/path", "score": 42}}
    second = {"id": str(uuid.uuid4()), "title": "Second artifact", "metadata": {}}
    item_path = "/artifacts?id=eq." + first["id"]

    def verify():
        result, _ = http(item_path, token())
        assert len(result) == 1 and result[0]["title"] == first["title"] and result[0]["metadata"] == {"score": 43}
        http(item_path, token(), "DELETE", expected=403)
        spec, _ = http("/", token())
        # Native OpenAPI filters visible relations, not individual CRUD methods.
        # Actual PostgreSQL denials above establish the reader's permissions.
        assert "/artifacts" in spec["paths"]
        assert set(spec["definitions"]["artifacts"]["properties"]) == {"id", "title", "metadata", "created_at"}

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        for jwt in (None, token(signing_key="incorrect" * 8), token(audience="other"), token(expires=int(time.time()) - 60)):
            http("/artifacts", jwt, expected=401)
        http("/artifacts", token("postgres"), expected=403)
        http("/artifacts", token(), "POST", first, expected=403)
        result, _ = http("/artifacts", token("ods_writer"), "POST", [first, second], expected=201, headers={"Prefer": "return=representation"})
        assert len(result) == 2 and result[0]["title"] == first["title"]
        http("/artifacts", token("ods_writer"), "POST", first, expected=409)
        result, headers = http("/artifacts?order=title", token(), headers={"Range": "0-0", "Prefer": "count=exact"}, expected=206)
        assert len(result) == 1 and headers["Content-Range"] == "0-0/2"
        failed_id = str(uuid.uuid4())
        http("/artifacts", token("ods_writer"), "POST", [{"id": failed_id, "title": "must roll back"}, {"title": " "}], expected=400)
        assert http("/artifacts?id=eq." + failed_id, token())[0] == []
        http("/artifacts", token("ods_writer"), "POST", {"title": "invalid metadata", "metadata": []}, expected=400)
        http(item_path, token("ods_writer"), "PATCH", {"metadata": {"score": 43}}, expected=204)
        verify()
        print("Native JWT/audience/expiry, PostgreSQL role denials, Unicode REST, ranges and atomic batch rollback verified", flush=True)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        verify()
        inspected = json.loads(run("docker", "inspect", project + "-postgrest-db").stdout)[0]
        address = inspected["NetworkSettings"]["Networks"][project + "-private"]["IPAddress"]
        rotated_password = secrets.token_urlsafe(32) + "'\\$ @:;[]"
        with psycopg.connect(host=address, dbname="ods", user="postgres", password=values["POSTGREST_POSTGRES_PASSWORD"], connect_timeout=5, autocommit=True) as connection:
            flags = connection.execute("SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = 'authenticator'").fetchone()
            assert flags == (False, False, False, False, False)
            connection.execute(sql.SQL("ALTER ROLE authenticator PASSWORD {}").format(sql.Literal(rotated_password)))
        old_token = token()
        values["POSTGREST_JWT_SECRET"] = secrets.token_urlsafe(32)
        api["environment"].update(PGPASSWORD=rotated_password.replace("$", "$$"), PGRST_JWT_SECRET=values["POSTGREST_JWT_SECRET"])
        database["environment"]["POSTGREST_DB_PASSWORD"] = rotated_password.replace("$", "$$")
        save()
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        http("/artifacts", old_token, expected=401)
        verify()
        run(*command, "stop", "--timeout", "45")
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 70, 70)
        database["volumes"][0]["source"] = str(restored)
        save()
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        verify()
        http("/artifacts?id=eq." + second["id"], token("ods_writer"), "DELETE", expected=204)
        assert http("/artifacts", token())[0][0]["id"] == first["id"]
        print("Recreation, punctuation-safe native database/JWT rotation, cold data/grant restore and REST deletion verified", flush=True)
    finally:
        print(run(*command, "logs", "--tail", "12").stdout)
        run(*command, "down", "--volumes")
