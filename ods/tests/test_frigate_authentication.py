"""The published Frigate UI must use its authenticated TLS listener."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/frigate"


def test_catalog_keeps_browser_tls_separate_from_internal_health(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
                    "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"]
                 if item["id"] == "frigate")
    assert entry["ui_scheme"] == "https"
    assert entry["port"] == 5000
    assert entry["external_port_default"] == 8971
    assert entry["health_endpoint"] == "/api/version"
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())["extensions"]
    assert entry == next(item for item in checked if item["id"] == "frigate")


@pytest.mark.skipif(os.environ.get("ODS_TEST_FRIGATE_AUTH") != "1",
                    reason="Opt-in Docker authenticated listener and account persistence test")
def test_published_listener_requires_login_after_recreation(tmp_path):
    name = "ods-frigate-auth-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/frigate"
    shutil.copytree(SERVICE, installed)
    config = tmp_path / "data/frigate/config"
    config.mkdir(parents=True)
    (config / "config.yml").write_text("mqtt:\n  enabled: false\ncameras: {}\n")
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  frigate:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8971"]
networks:
  ods-network:
    name: {name}
''')
    environment = {**os.environ, "FRIGATE_RTSP_PASSWORD": uuid.uuid4().hex}
    base_command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
                    "-f", str(installed / "compose.yaml")]
    command = [*base_command, "-f", str(overlay)]

    def run(*args):
        result = subprocess.run(args, env=environment, capture_output=True, text=True, timeout=480)
        diagnostics = ""
        if result.returncode != 0 and "up" in args:
            logs = subprocess.run(["docker", "logs", "--tail", "80", name],
                                  capture_output=True, text=True, timeout=30)
            diagnostics = re.sub(r"Password: [^\r\n]+", "Password: [redacted]", logs.stdout + logs.stderr)
        assert result.returncode == 0, result.stderr + diagnostics
        return result.stdout.strip()

    # Check the shipped mapping before replacing ports with isolated test ports.
    plan = json.loads(run(*base_command, "config", "--format", "json"))["services"]["frigate"]
    assert not any(port["target"] == 5000 for port in plan["ports"])
    browser_port = next(port for port in plan["ports"] if port["target"] == 8971)
    assert browser_port["host_ip"] == "127.0.0.1"
    run("docker", "network", "create", name)
    try:
        password = None
        for _ in range(2):
            run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "360")
            if password is None:
                # Read only this disposable instance's generated credential; do not print it.
                log = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=30)
                assert log.returncode == 0
                match = re.search(r"Password: ([a-f0-9]{32})", log.stdout + log.stderr)
                assert match is not None, "Frigate did not report its initial fixture account"
                password = match.group(1)
            origin = "https://" + run(*command, "port", "frigate", "8971")
            # This fixture uses the upstream-generated self-signed certificate.
            with httpx.Client(base_url=origin, verify=False, timeout=30) as client:
                assert client.get("/api/config").status_code == 401
                invalid = client.post("/api/login", json={"user": "admin", "password": "incorrect-fixture"})
                assert invalid.status_code == 401
                login = client.post("/api/login", json={"user": "admin", "password": password})
                assert login.status_code == 200
                assert client.get("/api/config").status_code == 200
                assert client.get("/api/users").status_code == 200
        run("docker", "exec", name, "test", "-s", "/config/.jwt_secret")
    finally:
        run(*command, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
