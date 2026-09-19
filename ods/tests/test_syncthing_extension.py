"""Real first-boot authentication and two-device synchronization contract."""

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/syncthing"


def test_syncthing_catalog_exposes_device_management(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
                    "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"] if item["id"] == "syncthing")
    assert (entry["port"], entry["external_port_default"], entry["health_endpoint"]) == (8384, 7831, "/rest/noauth/health")
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())["extensions"]
    assert entry == next(item for item in checked if item["id"] == "syncthing")


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=240)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class Device:
    def __init__(self, root, network):
        self.root = root
        self.name = network + "-" + root.name
        installed = root / "extensions/services/syncthing"
        shutil.copytree(SERVICE, installed)
        self.data = root / "data/syncthing"
        self.data.mkdir(parents=True)
        self.password = secrets.token_hex(20) + "<&\"'"
        self.envfile = root / ".env"
        self.envfile.write_text("SYNCTHING_ADMIN_PASSWORD=" + self.password + "\n")
        overlay = root / "isolation.yaml"
        overlay.write_text(f'''services:
  syncthing:
    container_name: {self.name}
    ports: !override ["127.0.0.1:0:8384"]
networks:
  ods-network:
    name: {network}
''')
        self.compose = ["docker", "compose", "--project-name", self.name, "--project-directory", str(root),
                        "--env-file", str(self.envfile), "-f", str(installed / "compose.yaml"), "-f", str(overlay)]
        self.image = json.loads(run(*self.compose, "config", "--format", "json"))["services"]["syncthing"]["image"]
        # Mirror manifest container_uid preparation without giving the app root.
        run("docker", "run", "--rm", "--user", "0:0", "--entrypoint", "chown",
            "-v", str(self.data) + ":/fixture", self.image, "1000:1000", "/fixture")

    def start(self):
        result = subprocess.run([*self.compose, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120"],
                                capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            logs = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True, timeout=15)
            print(result.stderr + logs.stdout + logs.stderr)
        assert result.returncode == 0, result.stderr
        self.origin = "http://" + run(*self.compose, "port", "syncthing", "8384")
        config = ET.fromstring(run("docker", "exec", self.name, "cat", "/var/syncthing/config/config.xml"))
        self.key = config.findtext("gui/apikey")
        assert self.key
        assert config.findtext("gui/password").startswith("$2")
        self.client = httpx.Client(base_url=self.origin, headers={"X-API-Key": self.key}, timeout=15)
        self.identifier = self.api("GET", "/rest/system/status")["myID"]

    def api(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        assert response.status_code == 200, response.text
        return response.json() if response.content else None

    def assert_auth(self):
        assert httpx.get(self.origin + "/rest/system/status").status_code == 403
        with httpx.Client(base_url=self.origin, timeout=10) as browser:
            response = browser.post("/rest/noauth/auth/password", json={"username": "admin", "password": self.password})
            assert response.status_code == 204, response.text
            assert browser.cookies
        wrong = httpx.post(self.origin + "/rest/noauth/auth/password",
                           json={"username": "admin", "password": "wrong-password"})
        assert wrong.status_code == 403

    def assert_private_options(self):
        options = self.api("GET", "/rest/config/options")
        for key in ("globalAnnounceEnabled", "localAnnounceEnabled", "relaysEnabled", "natEnabled",
                    "crashReportingEnabled", "startBrowser"):
            assert options[key] is False, key
        assert options["urAccepted"] == -1
        assert options["autoUpgradeIntervalH"] == 0
        assert options["listenAddresses"] == ["tcp://0.0.0.0:22000"]

    def close(self):
        if hasattr(self, "client"):
            self.client.close()
        run(*self.compose, "down", "--timeout", "10")


def wait_for_content(device, name, expected):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        # Read as the service user so CI host filesystem ownership is irrelevant.
        result = subprocess.run(["docker", "exec", device.name, "cat", "/var/syncthing/files/shared/" + name],
                                capture_output=True, timeout=15)
        if result.returncode == 0 and result.stdout == expected:
            return
        time.sleep(1)
    pytest.fail("Peer did not receive the expected file bytes within 90 seconds")


@pytest.mark.skipif(os.environ.get("ODS_TEST_SYNCTHING_DOCKER") != "1",
                    reason="Opt-in two-device synchronization with the pinned image")
def test_private_pairing_sync_and_identity_survive_recreation(tmp_path):
    network = "ods-sync-test-" + uuid.uuid4().hex[:10]
    run("docker", "network", "create", network)
    devices = []
    try:
        for label in ("a", "b"):
            device = Device(tmp_path / label, network)
            devices.append(device)
            if label == "a":
                for invalid, message in (("x" * 73, "password length exceeds 72 bytes"),
                                         ("first\nsecond", "must be a single line")):
                    rejected = subprocess.run([*device.compose, "run", "--rm", "--no-deps",
                                               "-e", "SYNCTHING_ADMIN_PASSWORD=" + invalid, "syncthing"],
                                              capture_output=True, text=True, timeout=90)
                    assert rejected.returncode != 0
                    assert message in rejected.stdout + rejected.stderr
                    assert not (device.data / "config").exists()
                    assert not list(device.data.glob(".ods-initialize-*"))
            device.start()
            device.assert_auth()
            device.assert_private_options()
            assert device.api("GET", "/rest/config/folders") == []
        a, b = devices
        identities = [device.identifier for device in devices]
        assert identities[0] != identities[1]
        for local, remote in ((a, b), (b, a)):
            local.api("POST", "/rest/config/devices", json={"deviceID": remote.identifier,
                      "name": remote.name, "addresses": [f"tcp://{remote.name}:22000"],
                      "autoAcceptFolders": False})
            local.api("POST", "/rest/config/folders", json={"id": "shared", "label": "Explicit shared data",
                      "path": "/var/syncthing/files/shared", "type": "sendreceive",
                      "devices": [{"deviceID": local.identifier}, {"deviceID": remote.identifier}],
                      "fsWatcherEnabled": True, "rescanIntervalS": 5})
            run("docker", "exec", local.name, "mkdir", "-p", "/var/syncthing/files/shared")
        for iteration in range(2):
            if iteration:
                for device in devices:
                    device.client.close()
                    device.envfile.write_text("SYNCTHING_ADMIN_PASSWORD=changed-bootstrap-value\n")
                    device.start()
                    device.assert_auth()  # Original persisted password still wins.
                    device.assert_private_options()
                assert [device.identifier for device in devices] == identities
                wait_for_content(b, "tài liệu.bin", payload)
            payload = b"ODS binary\x00\xff\r\n" * (128 + iteration)
            source = a.root / "source.bin"
            source.write_bytes(payload)
            run("docker", "cp", str(source), a.name + ":/var/syncthing/files/shared/tài liệu.bin")
            a.api("POST", "/rest/db/scan", params={"folder": "shared"})
            wait_for_content(b, "tài liệu.bin", payload)
            # Synchronization is bidirectional, not a one-sided copy fixture.
            run("docker", "exec", b.name, "sh", "-c", "printf peer-reply > /var/syncthing/files/shared/reply.txt")
            b.api("POST", "/rest/db/scan", params={"folder": "shared"})
            wait_for_content(a, "reply.txt", b"peer-reply")
    finally:
        for device in devices:
            device.close()
        run("docker", "network", "rm", network)
