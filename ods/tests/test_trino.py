"""Trino plans and opt-in HTTPS federation against a native PostgreSQL fixture."""

import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/trino"
KEYS = ("TRINO_PASSWORD_HASH", "TRINO_INTERNAL_SECRET", "TRINO_TLS_PASSWORD")


def render(tmp_path, values=None, bind=None, port="8448", check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("TRINO_") and key != "BIND_ADDRESS"}
    env["TRINO_HTTPS_PORT"] = port
    for key, value in zip(KEYS, values or ("fixture-hash", "fixture-internal-secret", "fixture-tls-password")):
        if value is not None:
            env[key] = value
    if bind is not None:
        env["BIND_ADDRESS"] = bind
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port", ["8448", "18448"])
def test_only_https_is_published_and_required_secrets_are_declared(tmp_path, bind, port):
    service = json.loads(render(tmp_path, bind=bind, port=port).stdout)["services"]["trino"]
    assert [(item["target"], item["host_ip"], item["published"]) for item in service["ports"]] == [(8443, bind or "127.0.0.1", port)]
    assert service["user"] == "1000:1000" and service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "trino")
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == set(KEYS)


@pytest.mark.parametrize("missing", range(3))
@pytest.mark.parametrize("value", [None, ""])
def test_each_security_setting_is_required(tmp_path, missing, value):
    values = ["fixture-hash", "fixture-internal-secret", "fixture-tls-password"]
    values[missing] = value
    result = render(tmp_path, values, check=False)
    assert result.returncode != 0 and KEYS[missing] in result.stderr


@pytest.mark.skipif(os.getenv("ODS_TEST_TRINO") != "1", reason="Opt-in real TLS SQL federation")
def test_native_https_auth_read_only_federation_recreation_and_tls_restore(tmp_path):
    import bcrypt

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 1000/70 storage")
    project = "ods-q20-trino-" + uuid.uuid4().hex[:12]
    password, internal, tls_password, pg_password = [secrets.token_hex(16) for _ in range(4)]
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()
    tls_dir = tmp_path / "data/trino/tls"
    tls_dir.mkdir(parents=True)
    pg_dir = tmp_path / "postgres"
    pg_dir.mkdir()
    os.chown(tls_dir, 1000, 1000)
    os.chown(pg_dir, 70, 70)
    recipe = tmp_path / "recipe"
    shutil.copytree(EXTENSION, recipe)
    (recipe / "catalog/fixture.properties").write_text(
        "connector.name=postgresql\nconnection-url=jdbc:postgresql://fixture-db:5432/fixture\n"
        "connection-user=fixture\nconnection-password=" + pg_password + "\n")
    plan = json.loads(render(tmp_path, [password_hash, internal, tls_password]).stdout)
    app = plan["services"]["trino"]
    app.update(container_name=project, restart="no", labels={"io.ods.quality20.validation": "true"},
               depends_on={"fixture-db": {"condition": "service_healthy"}})
    app["ports"][0]["published"] = "0"
    next(mount for mount in app["volumes"] if mount["target"] == "/etc/ods-trino")["source"] = str(recipe)
    plan["networks"] = {"ods-network": {"name": project + "-network"}}
    plan["services"]["fixture-db"] = {
        "image": "postgres:17.11-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73",
        "container_name": project + "-db", "user": "70:70", "restart": "no", "networks": ["ods-network"],
        "environment": {"POSTGRES_USER": "fixture", "POSTGRES_DB": "fixture", "POSTGRES_PASSWORD": pg_password},
        "volumes": [{"type": "bind", "source": str(pg_dir), "target": "/var/lib/postgresql/data"}],
        "tmpfs": ["/var/run/postgresql:uid=70,gid=70"],
        "healthcheck": {"test": ["CMD", "pg_isready", "-U", "fixture", "-d", "fixture"], "interval": "2s", "timeout": "2s", "retries": 30},
    }
    config = tmp_path / "isolated.json"
    config.write_text(json.dumps(plan))
    config.chmod(0o600)
    command = ["docker", "compose", "-p", project, "-f", str(config)]

    def run(*args, stdin=None, check=True):
        result = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=300)
        assert not check or result.returncode == 0, result.stdout + result.stderr
        return result

    def psql(sql):
        return run("docker", "exec", "-i", project + "-db", "psql", "-U", "fixture", "-d", "fixture", "-v", "ON_ERROR_STOP=1", "-At", stdin=sql).stdout.strip()

    def base():
        binding = json.loads(run("docker", "inspect", project).stdout)[0]["NetworkSettings"]["Ports"]["8443/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return "https://127.0.0.1:" + binding["HostPort"]

    def http(path, method="GET", sql=None, credential=None, user="ods", expected=200):
        origin = base()
        url = origin + path if path.startswith("/") else path
        assert urllib.parse.urlsplit(url)[:2] == urllib.parse.urlsplit(origin)[:2], "Continuation changed TLS origin"
        context = ssl.create_default_context(cafile=str(tls_dir / "server.crt"))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
        headers = {"X-Trino-User": user, "Content-Type": "text/plain; charset=utf-8"}
        if credential is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(("ods:" + credential).encode()).decode()
        request = urllib.request.Request(url, method=method, data=sql.encode() if sql is not None else None, headers=headers)
        try:
            response = opener.open(request, timeout=90)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            assert response.status == expected, f"{method} {path}: HTTP {response.status}: {body[:300]!r}"
            return json.loads(body) if body and response.headers.get_content_type() == "application/json" else body.decode()

    def query(sql, *, failure=None, user="ods"):
        result = http("/v1/statement", "POST", sql, password, user=user)
        rows = result.get("data", [])
        deadline = time.monotonic() + 120
        # Native protocol pagination, not a retry of a failed request.
        while result.get("nextUri"):
            assert time.monotonic() < deadline, "Query exceeded qualification deadline"
            result = http(result["nextUri"], credential=password, user=user)
            rows.extend(result.get("data", []))
        if failure:
            assert result["error"]["errorName"] == failure, result
        else:
            assert "error" not in result, result
        return rows

    federation = "SELECT n.name, f.label FROM tpch.tiny.nation n JOIN fixture.public.readings f ON n.nationkey = f.nation_id"
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "180")
        certificate = (tls_dir / "server.crt").read_bytes()
        assert http("/v1/info")["starting"] is False
        http("/v1/statement", "POST", "SELECT 1", expected=401)
        http("/v1/statement", "POST", "SELECT 1", "incorrect", expected=401)
        query("SELECT 1", user="admin", failure="PERMISSION_DENIED")
        for extra, status in (([], "403"), (["-H", "X-Forwarded-Proto: https"], "406")):
            denied = run("docker", "exec", project, "curl", "--noproxy", "*", "-sS", "-w", "\n%{http_code}",
                         "-H", "X-Trino-User: ods", "-H", "Content-Type: text/plain", "-H", "Accept: application/json",
                         *extra, "--data", "SELECT 1", "http://127.0.0.1:8080/v1/statement")
            assert denied.stdout.rsplit("\n", 1)[-1] == status, denied.stdout
            if extra:
                assert "does not allow processing of the X-Forwarded-Proto header" in denied.stdout
        psql("CREATE TABLE readings (nation_id integer, label text); INSERT INTO readings VALUES (0, 'Xin chào');")
        assert query(federation) == [["ALGERIA", "Xin chào"]]
        query("INSERT INTO fixture.public.readings VALUES (1, 'forbidden')", failure="PERMISSION_DENIED")
        query("CREATE SCHEMA fixture.forbidden", failure="PERMISSION_DENIED")
        query("CALL fixture.system.execute(query => 'DELETE FROM readings')", failure="PERMISSION_DENIED")
        query("SELECT * FROM TABLE(fixture.system.query(query => 'DELETE FROM readings RETURNING label'))", failure="PERMISSION_DENIED")
        assert psql("SELECT count(*) FROM readings;") == "1"
        print("Verified native TLS, rejected HTTP bypass/impersonation, and read-only cross-catalog SQL", flush=True)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180", "trino")
        assert (tls_dir / "server.crt").read_bytes() == certificate
        assert query(federation) == [["ALGERIA", "Xin chào"]]
        previous = password
        password = secrets.token_hex(16)
        app["environment"]["TRINO_PASSWORD_HASH"] = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode().replace("$", "$$")
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180", "trino")
        http("/v1/statement", "POST", "SELECT 1", previous, expected=401)
        assert query(federation) == [["ALGERIA", "Xin chào"]]
        run(*command, "stop", "--timeout", "30", "trino")
        restored = tmp_path / "restored-tls"
        shutil.copytree(tls_dir, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 1000, 1000)
        next(mount for mount in app["volumes"] if mount["target"] == "/var/lib/ods-trino/tls")["source"] = str(restored)
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180", "trino")
        assert (restored / "server.crt").read_bytes() == certificate
        assert query(federation) == [["ALGERIA", "Xin chào"]]
        print("Recreation, password rotation, and cold TLS identity restore verified", flush=True)
        keystore = (restored / "server.p12").read_bytes()
        app["environment"]["TRINO_TLS_PASSWORD"] = secrets.token_hex(16)
        config.write_text(json.dumps(plan))
        mismatch = run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60", "trino", check=False)
        assert mismatch.returncode != 0
        assert "keystore password was incorrect" in run(*command, "logs", "trino").stdout.lower()
        assert (restored / "server.p12").read_bytes() == keystore
        app["environment"]["TRINO_TLS_PASSWORD"] = tls_password
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180", "trino")
        assert (restored / "server.crt").read_bytes() == certificate
        assert query(federation) == [["ALGERIA", "Xin chào"]]
    finally:
        print(run(*command, "logs", "--tail", "25").stdout)
        run(*command, "down", "--volumes")
