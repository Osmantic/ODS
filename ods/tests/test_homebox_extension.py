"""Real inventory, group isolation, attachment storage and signup lifecycle."""

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import uuid

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/homebox"


def test_homebox_catalog_exposes_inventory(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
                    "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"] if item["id"] == "homebox")
    assert (entry["port"], entry["external_port_default"], entry["health_endpoint"]) == (7745, 7834, "/api/v1/status")
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())["extensions"]
    assert entry == next(item for item in checked if item["id"] == "homebox")


@pytest.mark.skipif(os.environ.get("ODS_TEST_HOMEBOX_DOCKER") != "1",
                    reason="Opt-in exact-image private inventory and attachment lifecycle")
def test_private_inventory_survives_registration_closure_and_recreation(tmp_path):
    name = "ods-homebox-test-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/homebox"
    shutil.copytree(SERVICE, installed)
    data = tmp_path / "data/homebox"
    data.mkdir(parents=True)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  homebox:
    container_name: {name}
    ports: !override ["127.0.0.1:0:7745"]
networks:
  ods-network:
    name: {name}
''')
    compose = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]
    environment = {**os.environ, "HOMEBOX_ALLOW_REGISTRATION": "false",
                   "HOMEBOX_API_KEY_PEPPER": secrets.token_hex(32)}

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, env=environment, timeout=240)
        if result.returncode != 0 and "up" in args:
            print(result.stderr, flush=True)
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=60)
            print(logs.stdout + logs.stderr)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    plan = json.loads(run(*compose, "config", "--format", "json"))["services"]["homebox"]
    assert plan["user"] == "65532:65532" and plan["read_only"] is True
    assert plan["ports"][0]["host_ip"] == "127.0.0.1"
    assert plan["environment"]["HBOX_OPTIONS_ALLOW_REGISTRATION"] == "false"
    run("docker", "run", "--rm", "--user", "0:0", "--entrypoint", "chown",
        "-v", str(data) + ":/fixture", plan["image"], "-R", "65532:65532", "/fixture")
    run("docker", "network", "create", name)
    password = secrets.token_hex(20)
    receipt = "Hóa đơn thiết bị ODS\nQuantity: 2\n".encode()
    try:
        for iteration in range(2):
            environment["HOMEBOX_ALLOW_REGISTRATION"] = "true" if not iteration else "false"
            run(*compose, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
            origin = "http://" + run(*compose, "port", "homebox", "7745")
            with httpx.Client(base_url=origin + "/api/v1", timeout=30) as client:
                assert client.get("/entities").status_code == 401
                if not iteration:
                    for account in ("owner", "outsider"):
                        registered = client.post("/users/register", json={"name": account,
                            "email": account + "@example.test", "password": password})
                        assert registered.status_code == 204, registered.text
                else:
                    closed = client.post("/users/register", json={"name": "blocked",
                        "email": "blocked@example.test", "password": password})
                    assert closed.status_code == 403 and "registration disabled" in closed.text
                wrong = client.post("/users/login", json={"username": "owner@example.test", "password": "wrong"})
                assert wrong.status_code == 401
                credentials = {}
                for account in ("owner", "outsider"):
                    login = client.post("/users/login", json={"username": account + "@example.test", "password": password})
                    assert login.status_code == 200, login.text
                    credentials[account] = {"Authorization": login.json()["token"]}
                    client.cookies.clear()
                headers = credentials["owner"]
                if not iteration:
                    key = client.post("/users/self/api-keys", headers=headers, json={"name": "ODS inventory workflow"})
                    assert key.status_code == 201, key.text
                    key_id = key.json()["id"]
                    api_headers = {"Authorization": "Bearer " + key.json()["token"]}
                assert client.get("/users/self", headers=api_headers).status_code == 200
                if not iteration:
                    kind = client.post("/entity-types", headers=headers, json={"name": "Storage room", "isLocation": True})
                    assert kind.status_code == 201, kind.text
                    location = client.post("/entities", headers=headers,
                        json={"name": "Phòng làm việc", "entityTypeId": kind.json()["id"]})
                    assert location.status_code == 201, location.text
                    location_id = location.json()["id"]
                    item = client.post("/entities", headers=headers, json={"name": "ODS microphone",
                        "description": "Thiết bị ghi âm", "parentId": location_id, "quantity": 2})
                    assert item.status_code == 201, item.text
                    item_id = item.json()["id"]
                    upload = client.post(f"/entities/{item_id}/attachments", headers=headers,
                        data={"name": "hóa-đơn.txt", "type": "receipt", "primary": "false"},
                        files={"file": ("receipt.txt", receipt, "text/plain")})
                    assert upload.status_code == 201, upload.text
                    attachment_id = upload.json()["attachments"][0]["id"]
                details = client.get("/entities/" + item_id, headers=api_headers)
                assert details.status_code == 200, details.text
                assert details.json()["description"] == "Thiết bị ghi âm"
                assert details.json()["quantity"] == 2 and details.json()["parent"]["id"] == location_id
                assert details.json()["attachments"][0]["title"] == "hóa-đơn.txt"
                attachment_path = f"/entities/{item_id}/attachments/{attachment_id}"
                downloaded = client.get(attachment_path, headers=headers)
                assert downloaded.status_code == 200 and downloaded.content == receipt
                assert client.get(attachment_path).status_code == 401
                assert client.get("/entities/" + item_id, headers=credentials["outsider"]).status_code == 404
                denied = client.get(attachment_path, headers=credentials["outsider"])
                # 0.26.2 maps a group-scoped attachment lookup miss to 500.
                # Record that upstream limitation rather than claiming 404.
                assert denied.status_code == 500, denied.text
                assert receipt not in denied.content
                if iteration:
                    revoked = client.delete("/users/self/api-keys/" + key_id, headers=headers)
                    assert revoked.status_code == 204, revoked.text
                    assert client.get("/users/self", headers=api_headers).status_code == 401
    finally:
        run(*compose, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
