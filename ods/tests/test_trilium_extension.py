"""Actual notebook setup, authenticated ETAPI and persistent rich-note state."""

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
SERVICE = ROOT / "extensions/library/services/trilium"


def test_trilium_catalog_exposes_notebook(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
                    "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"] if item["id"] == "trilium")
    assert (entry["port"], entry["external_port_default"], entry["health_endpoint"]) == (8080, 7832, "/api/health-check")
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())["extensions"]
    assert entry == next(item for item in checked if item["id"] == "trilium")


@pytest.mark.skipif(os.environ.get("ODS_TEST_TRILIUM_DOCKER") != "1",
                    reason="Opt-in exact-image notebook setup and persistent ETAPI")
def test_first_run_password_notes_and_attachments_survive_recreation(tmp_path):
    name = "ods-trilium-test-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/trilium"
    shutil.copytree(SERVICE, installed)
    data = tmp_path / "data/trilium"
    data.mkdir(parents=True)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  trilium:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8080"]
networks:
  ods-network:
    name: {name}
''')
    compose = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=240)
        if result.returncode != 0 and "up" in args:
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=15)
            print(result.stderr + logs.stdout + logs.stderr)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    plan = json.loads(run(*compose, "config", "--format", "json"))["services"]["trilium"]
    assert plan["user"] == "1000:1000"
    assert plan["read_only"] is True
    assert plan["ports"][0]["host_ip"] == "127.0.0.1"
    run("docker", "run", "--rm", "--user", "0:0", "--entrypoint", "chown",
        "-v", str(data) + ":/fixture", plan["image"], "1000:1000", "/fixture")
    run("docker", "network", "create", name)
    password = secrets.token_hex(20)
    title = "Nghiên cứu local & notes"
    content = "<h2>Local research</h2><p>Tài liệu <strong>preserved</strong>.</p>"
    try:
        for iteration in range(2):
            run(*compose, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
            origin = "http://" + run(*compose, "port", "trilium", "8080")
            with httpx.Client(base_url=origin, timeout=30) as client:
                status = client.get("/api/setup/status")
                assert status.status_code == 200, status.text
                if not iteration:
                    assert status.json()["isInitialized"] is False
                    created = client.post("/api/setup/new-document", params={"skipDemoDb": "1"}, json={"locale": "en"})
                    assert created.status_code == 204, created.text
                    password_set = client.post("/set-password", data={"password1": password, "password2": password})
                    assert password_set.status_code == 302, password_set.text
                else:
                    assert status.json()["isInitialized"] is True
                anonymous = client.get("/etapi/notes/root")
                assert anonymous.status_code == 401, anonymous.text
                wrong = client.post("/etapi/auth/login", json={"password": "wrong-password"})
                assert wrong.status_code == 401, wrong.text
                login = client.post("/etapi/auth/login", json={"password": password, "tokenName": "ODS fixture"})
                assert login.status_code == 201, login.text
                token = login.json()["authToken"]
                headers = {"Authorization": token}
                if not iteration:
                    created = client.post("/etapi/create-note", headers=headers, json={"parentNoteId": "root",
                                          "title": title, "type": "text", "content": content})
                    assert created.status_code == 201, created.text
                    note_id = created.json()["note"]["noteId"]
                    child = client.post("/etapi/create-note", headers=headers, json={"parentNoteId": note_id,
                                        "title": "Child evidence", "type": "text", "content": "nested evidence"})
                    assert child.status_code == 201, child.text
                    child_id = child.json()["note"]["noteId"]
                    attachment = client.post("/etapi/attachments", headers=headers, json={"ownerId": note_id,
                                             "role": "file", "mime": "text/plain", "title": "Tài liệu.txt",
                                             "content": "Unicode attachment: nghiên cứu"})
                    assert attachment.status_code == 201, attachment.text
                    attachment_id = attachment.json()["attachmentId"]
                note = client.get("/etapi/notes/" + note_id, headers=headers)
                assert note.status_code == 200 and note.json()["title"] == title
                assert child_id in note.json()["childNoteIds"]
                stored = client.get(f"/etapi/notes/{note_id}/content", headers=headers)
                assert stored.status_code == 200 and "Tài liệu" in stored.text and "preserved" in stored.text
                attached = client.get(f"/etapi/attachments/{attachment_id}/content", headers=headers)
                assert attached.status_code == 200 and attached.text == "Unicode attachment: nghiên cứu"
                # The token may be explicitly revoked without deleting the notes.
                assert client.post("/etapi/auth/logout", headers=headers).status_code == 204
                assert client.get("/etapi/notes/" + note_id, headers=headers).status_code == 401
    finally:
        run(*compose, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
