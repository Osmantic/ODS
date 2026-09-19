"""Real admin API and SFTP transfers through the installed SFTPGo recipe."""

from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/sftpgo"


def test_sftpgo_catalog_contains_install_credentials(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
                    "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"] if item["id"] == "sftpgo")
    assert entry["external_port_default"] == 7828
    assert entry["health_endpoint"] == "/healthz"
    assert any(item["key"] == "SFTPGO_ADMIN_PASSWORD" and item["required"] for item in entry["env_vars"])
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())["extensions"]
    assert entry == next(item for item in checked if item["id"] == "sftpgo")


@pytest.mark.skipif(os.environ.get("ODS_TEST_SFTPGO_DOCKER") != "1",
                    reason="Opt-in exact-image admin, SFTP isolation and persistence test")
def test_sftp_accounts_files_and_host_identity_survive_recreation(tmp_path, monkeypatch):
    import paramiko

    name = "ods-sftpgo-test-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/sftpgo"
    shutil.copytree(SERVICE, installed)
    data = tmp_path / "data/sftpgo"
    (data / "state").mkdir(parents=True)
    (data / "files").mkdir()
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  sftpgo:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8080", "127.0.0.1:0:2022"]
networks:
  ods-network:
    name: {name}
''')
    command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]
    monkeypatch.delenv("SFTPGO_ADMIN_PASSWORD", raising=False)
    missing = subprocess.run([*command, "config"], capture_output=True, text=True, timeout=60)
    assert missing.returncode != 0
    assert "SFTPGO_ADMIN_PASSWORD" in missing.stderr
    admin_password = uuid.uuid4().hex + "-A9!"
    monkeypatch.setenv("SFTPGO_ADMIN_PASSWORD", admin_password)
    monkeypatch.setenv("SFTPGO_ADMIN_USER", "admin")
    user_password = uuid.uuid4().hex + "-B8!"

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=300)
        if result.returncode != 0 and "up" in args:
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=30)
            print(logs.stdout + logs.stderr)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    @contextmanager
    def connection(username, expected_key=None):
        host, port = run(*command, "port", "sftpgo", "2022").rsplit(":", 1)
        with paramiko.Transport((host, int(port))) as transport:
            transport.start_client(timeout=15)
            key = transport.get_remote_server_key().asbytes()
            if expected_key is not None:
                assert key == expected_key
            transport.auth_password(username, user_password)
            with paramiko.SFTPClient.from_transport(transport) as client:
                yield client, key

    plan = json.loads(run(*command, "config", "--format", "json"))["services"]["sftpgo"]
    assert {item["target"] for item in plan["ports"]} == {8080, 2022}
    assert all(item["host_ip"] == "127.0.0.1" for item in plan["ports"])
    assert plan["user"] == "1000:1000"
    # Mirror the manifest's host-agent ownership contract on this fixture only.
    run("docker", "run", "--rm", "--user", "0:0", "--entrypoint", "/bin/chown",
        "-v", str(data) + ":/fixture", plan["image"], "-R", "1000:1000", "/fixture")
    run("docker", "network", "create", name)
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        origin = "http://" + run(*command, "port", "sftpgo", "8080")
        with httpx.Client(base_url=origin, timeout=30) as api:
            assert api.get("/healthz").status_code == 200
            assert api.get("/api/v2/users").status_code == 401
            assert api.get("/api/v2/token", auth=("admin", "incorrect")).status_code == 401
            login = api.get("/api/v2/token", auth=("admin", admin_password))
            assert login.status_code == 200
            api.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            for username in ("alice", "bob"):
                created = api.post("/api/v2/users", json={
                    "username": username, "password": user_password, "status": 1,
                    "home_dir": "/srv/sftpgo/data/" + username,
                    "permissions": {"/": ["list", "download", "upload", "create_dirs", "delete_files"]},
                })
                assert created.status_code == 201, created.text[:200]

        filename = "Tài liệu.bin"
        content = bytes(range(256)) * 128
        with connection("alice") as (client, host_key):
            client.putfo(io.BytesIO(content), filename)
            with client.open(filename, "rb") as uploaded:
                assert uploaded.read() == content
        with connection("bob", host_key) as (client, _):
            assert filename not in client.listdir("/")
            with pytest.raises(FileNotFoundError):
                client.open("../alice/" + filename, "rb")

        monkeypatch.setenv("SFTPGO_ADMIN_PASSWORD", uuid.uuid4().hex + "-Changed!")
        run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
        origin = "http://" + run(*command, "port", "sftpgo", "8080")
        with httpx.Client(base_url=origin, timeout=30) as api:
            assert api.get("/api/v2/token", auth=("admin", admin_password)).status_code == 200
            assert api.get("/api/v2/token", auth=("admin", os.environ["SFTPGO_ADMIN_PASSWORD"])).status_code == 401
        with connection("alice", host_key) as (client, _):
            with client.open(filename, "rb") as restored:
                assert restored.read() == content
        with connection("bob", host_key) as (client, _):
            assert filename not in client.listdir("/")
    finally:
        run(*command, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
