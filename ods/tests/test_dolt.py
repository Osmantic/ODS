"""Dolt extension contract and opt-in native SQL/version-control lifecycle."""

import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/library/services/dolt"


def render(tmp_path, password, bind=None, port="3312", check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("DOLT_") and key != "BIND_ADDRESS"}
    env["DOLT_SQL_PORT"] = port
    if password is not None:
        env["DOLT_ROOT_PASSWORD"] = password
    if bind is not None:
        env["BIND_ADDRESS"] = bind
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return subprocess.run(["docker", "compose", "--env-file", str(empty), "--project-directory", str(tmp_path),
                           "-f", str(EXTENSION / "compose.yaml"), "config", "--format", "json"],
                          env=env, check=check, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "0.0.0.0", "192.0.2.42"])
@pytest.mark.parametrize("port", ["3312", "13312"])
def test_plan_publishes_only_authenticated_sql_and_preserves_operator_bindings(tmp_path, bind, port):
    password = secrets.token_hex(16) + "'$\\;"
    service = json.loads(render(tmp_path, password, bind, port).stdout)["services"]["dolt"]
    assert [(item["target"], item["host_ip"], item["published"]) for item in service["ports"]] == [(3306, bind or "127.0.0.1", port)]
    # Compose escapes dollars in its reusable config output; the live login
    # below checks the actual, single-dollar credential accepted by Dolt.
    assert service["environment"]["DOLT_ROOT_PASSWORD"] == password.replace("$", "$$")
    assert service["user"] == "1000:1000" and service["read_only"] is True
    assert service["entrypoint"] == ["tini", "--", "dolt"]
    assert service["command"] == ["sql-server", "--config", "/etc/dolt/ods-server.yaml"]
    mounts = {item["target"]: item["source"] for item in service["volumes"]}
    assert mounts["/var/lib/dolt"] == str(tmp_path / "data/dolt")
    assert mounts["/var/lib/dolt/.dolt"] == str(tmp_path / "data/dolt/.dolt")
    settings = yaml.safe_load((EXTENSION / "config/dolt-server.yaml").read_text())
    assert settings["behavior"]["dolt_transaction_commit"] is False
    assert "remotesapi" not in settings and "mcp_server" not in settings


@pytest.mark.parametrize("password", [None, ""])
def test_empty_root_password_cannot_publish_server(tmp_path, password):
    result = render(tmp_path, password, check=False)
    assert result.returncode != 0 and "DOLT_ROOT_PASSWORD" in result.stderr


def test_catalog_has_no_http_launch_and_runtime_scanner_keeps_health_port(tmp_path):
    catalog = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "dolt")
    assert entry["port"] == entry["external_port_default"] == 0
    assert entry["startup_check"] is True  # Port zero must not classify a daemon as a one-shot tool.
    assert next(item for item in entry["env_vars"] if item["key"] == "DOLT_ROOT_PASSWORD")["required"]
    enabled = tmp_path / "dolt"
    enabled.mkdir()
    for name in ("compose.yaml", "manifest.yaml"):
        shutil.copy2(EXTENSION / name, enabled / name)
    spec = importlib.util.spec_from_file_location("dolt_user_extensions", ROOT / "extensions/services/dashboard-api/user_extensions.py")
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    scanned = scanner.scan_user_extension_services(tmp_path)["dolt"]
    assert scanned["host"] == "dolt"
    assert scanned["health_port"] == 11112 and scanned["health"] == "/metrics"


@pytest.mark.skipif(os.getenv("ODS_TEST_DOLT") != "1", reason="Opt-in real Dolt lifecycle")
def test_sql_auth_versioned_branches_privileges_rotation_recreation_and_cold_restore(tmp_path):
    import pymysql

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Qualification prepares isolated UID 1000 storage")
    password = secrets.token_hex(16) + "'$\\;"
    rotated, reader_password = secrets.token_hex(16), secrets.token_hex(16)
    project = "ods-q20-dolt-" + uuid.uuid4().hex[:12]
    data = tmp_path / "data/dolt"
    data.mkdir(parents=True)
    os.chown(data, 1000, 1000)
    (data / ".dolt").mkdir()
    os.chown(data / ".dolt", 1000, 1000)
    shutil.copytree(EXTENSION / "config", tmp_path / "config")
    plan = json.loads(render(tmp_path, password).stdout)
    service = plan["services"]["dolt"]
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

    def connect(credential, user="root", database=None):
        binding = json.loads(run("docker", "inspect", project))[0]["NetworkSettings"]["Ports"]["3306/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        return pymysql.connect(host="127.0.0.1", port=int(binding["HostPort"]), user=user, password=credential,
                               database=database, autocommit=True, connect_timeout=5, read_timeout=15, write_timeout=15)

    def sql(connection, statement, arguments=None):
        with connection.cursor() as cursor:
            cursor.execute(statement, arguments)
            rows = cursor.fetchall()
            while cursor.nextset():
                cursor.fetchall()
            return rows

    def denied(credential, user="root"):
        with pytest.raises(pymysql.OperationalError) as error:
            connect(credential, user)
        assert error.value.args[0] == 1045

    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        denied("")
        denied("incorrect")
        denied(password, "ods")
        with connect(password) as connection:
            sql(connection, "CREATE DATABASE ods_fixture")
            sql(connection, "USE ods_fixture")
            sql(connection, "CREATE TABLE examples (id INT PRIMARY KEY, prompt TEXT)")
            sql(connection, "INSERT INTO examples VALUES (1, %s)", ("Xin chào",))
            baseline = sql(connection, "CALL DOLT_COMMIT('-Am', 'baseline', '--author', 'ODS Fixture <fixture@localhost>')")[0][0]
            sql(connection, "CALL DOLT_CHECKOUT('-b', 'candidate')")
            sql(connection, "UPDATE examples SET prompt = %s WHERE id = 1", ("Improved prompt",))
            candidate = sql(connection, "CALL DOLT_COMMIT('-Am', 'candidate', '--author', 'ODS Fixture <fixture@localhost>')")[0][0]
            assert candidate != baseline
            assert sql(connection, "SELECT prompt FROM examples AS OF %s", (baseline,)) == (("Xin chào",),)
            assert sql(connection, "SELECT prompt FROM examples") == (("Improved prompt",),)
            sql(connection, "CALL DOLT_CHECKOUT('main')")
            assert sql(connection, "SELECT prompt FROM examples") == (("Xin chào",),)
            sql(connection, "CREATE USER 'reader'@'%%' IDENTIFIED BY %s", (reader_password,))
            sql(connection, "GRANT SELECT ON ods_fixture.* TO 'reader'@'%'")
            sql(connection, "ALTER USER 'root'@'%%' IDENTIFIED BY %s", (rotated,))
        denied(password)
        with connect(reader_password, "reader", "ods_fixture") as connection:
            assert sql(connection, "SELECT prompt FROM examples") == (("Xin chào",),)
            with pytest.raises(pymysql.OperationalError) as error:
                sql(connection, "UPDATE examples SET prompt = 'forbidden'")
            assert error.value.args[0] == 1105 and "denied" in error.value.args[1].lower()
            assert sql(connection, "SELECT prompt FROM examples") == (("Xin chào",),)
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        denied(password)  # The old bootstrap environment cannot reset ALTER USER.
        with connect(rotated, database="ods_fixture") as connection:
            assert sql(connection, "SELECT prompt FROM examples AS OF %s", (candidate,)) == (("Improved prompt",),)
            assert sql(connection, "SELECT prompt FROM examples AS OF %s", (baseline,)) == (("Xin chào",),)
        assert "dss_concurrent_connections" in run("docker", "exec", project, "curl", "--fail", "--silent", "http://127.0.0.1:11112/metrics")
        for table in ("/proc/net/tcp", "/proc/net/tcp6"):
            for line in run("docker", "exec", project, "cat", table).splitlines()[1:]:
                fields = line.split()
                if fields[3] == "0A":
                    assert int(fields[1].rsplit(":", 1)[1], 16) not in (33060, 7007)
        run(*command, "stop", "--timeout", "30")
        restored = tmp_path / "restored"
        shutil.copytree(data, restored)
        for path in [restored, *restored.rglob("*")]:
            os.chown(path, 1000, 1000)
        for volume in service["volumes"]:
            if volume["target"] == "/var/lib/dolt":
                volume["source"] = str(restored)
            elif volume["target"] == "/var/lib/dolt/.dolt":
                volume["source"] = str(restored / ".dolt")
        config.write_text(json.dumps(plan))
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        denied(password)
        with connect(reader_password, "reader", "ods_fixture") as connection:
            assert sql(connection, "SELECT prompt FROM examples") == (("Xin chào",),)
        with connect(rotated, database="ods_fixture") as connection:
            assert sql(connection, "SELECT prompt FROM examples AS OF %s", (candidate,)) == (("Improved prompt",),)
            sql(connection, "DROP USER 'reader'@'%'")
        denied(reader_password, "reader")
    finally:
        print(run(*command, "logs", "--tail", "20"))
        run(*command, "down", "--volumes")
