"""Discover HedgeDoc and exercise its installed recipe at the HTTP boundary."""

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
SERVICE = ROOT / "extensions/library/services/hedgedoc"


def test_hedgedoc_catalog_exposes_setup_and_health_contract(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
                    "--output", str(output)], check=True)
    generated = json.loads(output.read_text())["extensions"]
    entry = next(item for item in generated if item["id"] == "hedgedoc")
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())["extensions"]
    assert entry == next(item for item in checked if item["id"] == "hedgedoc")
    assert (entry["port"], entry["external_port_default"]) == (3000, 7824)
    assert entry["health_endpoint"] == "/_health"
    assert {item["key"] for item in entry["env_vars"] if item.get("required")} == {
        "HEDGEDOC_DB_PASSWORD", "HEDGEDOC_SESSION_SECRET"}


@pytest.mark.skipif(os.environ.get("ODS_TEST_HEDGEDOC_DOCKER") != "1",
                    reason="Opt-in Docker account, private note and persistence test")
def test_hedgedoc_private_note_survives_container_recreation(tmp_path):
    name = f"ods-hedgedoc-test-{uuid.uuid4().hex[:10]}"
    installed = tmp_path / "extensions/services/hedgedoc"
    shutil.copytree(SERVICE, installed)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  hedgedoc:
    container_name: {name}
    ports: !override ["127.0.0.1:0:3000"]
  hedgedoc-db:
    container_name: {name}-db
networks:
  ods-network:
    name: {name}
''')
    command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("HEDGEDOC_")}

    def run(*args):
        result = subprocess.run(args, env=environment, capture_output=True, text=True, timeout=420)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    for missing in ("HEDGEDOC_DB_PASSWORD", "HEDGEDOC_SESSION_SECRET"):
        incomplete = {**environment, "HEDGEDOC_DB_PASSWORD": "fixture", "HEDGEDOC_SESSION_SECRET": "fixture"}
        del incomplete[missing]
        result = subprocess.run([*command, "config"], env=incomplete, capture_output=True, text=True, timeout=60)
        assert result.returncode != 0
        assert missing in result.stderr
    environment.update(HEDGEDOC_DB_PASSWORD="p@ss:/#" + uuid.uuid4().hex,
                       HEDGEDOC_SESSION_SECRET=uuid.uuid4().hex + uuid.uuid4().hex)
    default_plan = json.loads(run(*command, "config", "--format", "json"))["services"]
    assert default_plan["hedgedoc"]["environment"]["CMD_DOMAIN"] == "localhost:7824"
    environment["HEDGEDOC_PORT"] = "17824"
    custom_plan = json.loads(run(*command, "config", "--format", "json"))["services"]
    assert custom_plan["hedgedoc"]["environment"]["CMD_DOMAIN"] == "localhost:17824"
    assert not custom_plan["hedgedoc-db"].get("ports")
    assert custom_plan["hedgedoc"]["ports"][0]["host_ip"] == "127.0.0.1"
    email = "fixture@example.test"
    password = "Local-note-" + uuid.uuid4().hex
    markdown = "# Private fixture\n\nPersistent Markdown: café, Việt Nam.\n"
    run("docker", "network", "create", name)
    try:
        run(*command, "up", "-d", "--wait", "--wait-timeout", "300")
        run("docker", "exec", name, "bin/manage_users", "--add", email, "--pass", password)
        run("docker", "exec", name, "bin/manage_users", "--add", "other@example.test", "--pass", password)
        note_path = None
        for recreate in (False, True):
            if recreate:
                run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "300")
            origin = "http://" + run(*command, "port", "hedgedoc", "3000")
            with httpx.Client(base_url=origin, timeout=30) as client:
                assert client.get("/_health").json()["ready"] is True
                denied = client.post("/new", content=markdown, headers={"Content-Type": "text/markdown"})
                assert denied.status_code == 302
                assert denied.headers["location"] == "http://localhost:17824/"
                assert client.post("/register", data={"email": email, "password": password}).status_code == 404
                login = client.post("/login", data={"email": email, "password": password})
                assert login.status_code == 302
                # Upstream remembers the denied /new request in the session.
                assert login.headers["location"] == "http://localhost:17824/new"
                if note_path is None:
                    created = client.post("/new", content=markdown, headers={"Content-Type": "text/markdown"})
                    assert created.status_code == 302, created.text[:300]
                    note_path = httpx.URL(created.headers["location"]).path
                document = client.get(note_path + "/download")
                assert document.status_code == 200
                assert document.text == markdown
                upload = client.post("/uploadimage", files={"image": ("note.txt", b"private", "text/plain")})
                assert upload.status_code in (403, 404)
            with httpx.Client(base_url=origin, timeout=30) as anonymous:
                denied = anonymous.get(note_path + "/download")
                assert denied.status_code == 302
                assert denied.headers["location"] == "http://localhost:17824/"
            with httpx.Client(base_url=origin, timeout=30) as other:
                assert other.post("/login", data={"email": "other@example.test", "password": password}).status_code == 302
                assert other.get(note_path + "/download").status_code == 403
        assert (tmp_path / "data/hedgedoc/postgres/PG_VERSION").read_text().strip() == "17"
    finally:
        run(*command, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
