"""Installed Datasette recipe: private, immutable local dataset browsing."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/datasette"


def test_datasette_catalog_exposes_private_x86_recipe(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
                    "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"]
                 if item["id"] == "datasette")
    assert entry["external_port_default"] == 7827
    assert "apple" not in entry["gpu_backends"]
    assert any(item["key"] == "DATASETTE_SECRET" and item["required"] for item in entry["env_vars"])
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())["extensions"]
    assert entry == next(item for item in checked if item["id"] == "datasette")


@pytest.mark.skipif(os.environ.get("ODS_TEST_DATASETTE_DOCKER") != "1",
                    reason="Opt-in exact-image private SQL/CSV and immutable snapshot test")
def test_private_dataset_survives_recreation_without_source_mutation(tmp_path, monkeypatch):
    name = "ods-datasette-test-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/datasette"
    shutil.copytree(SERVICE, installed)
    data = tmp_path / "data/datasette"
    data.mkdir(parents=True)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  datasette:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8001"]
networks:
  ods-network:
    name: {name}
''')
    command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]
    monkeypatch.delenv("DATASETTE_SECRET", raising=False)
    missing = subprocess.run([*command, "config"], capture_output=True, text=True, timeout=60)
    assert missing.returncode != 0
    assert "DATASETTE_SECRET" in missing.stderr
    monkeypatch.setenv("DATASETTE_SECRET", uuid.uuid4().hex + uuid.uuid4().hex)

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=240)
        if result.returncode != 0 and "up" in args:
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=30)
            print(re.sub(r"token=[a-zA-Z0-9]+", "token=[redacted]", logs.stdout + logs.stderr))
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    run("docker", "network", "create", name)
    try:
        plan = json.loads(run(*command, "config", "--format", "json"))["services"]["datasette"]
        assert plan["ports"][0]["host_ip"] == "127.0.0.1"
        assert plan["read_only"] is True
        assert all(mount["read_only"] for mount in plan["volumes"])
        run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
        origin = "http://" + run(*command, "port", "datasette", "8001")
        login_path = re.search(r"/-/auth-token\?token=[a-zA-Z0-9]+", run("docker", "logs", name)).group()
        with httpx.Client(base_url=origin, follow_redirects=True, timeout=30) as client:
            assert client.get("/").status_code == 403
            assert client.get(login_path).status_code == 200
            assert client.get("/-/actor.json").json()["actor"] == {"id": "root"}
            assert client.get(login_path).status_code in (400, 403)
            cookies = client.cookies

        run(*command, "stop")
        snapshot = data / "research data.db"
        with sqlite3.connect(snapshot) as database:
            database.execute("CREATE TABLE docs (id INTEGER PRIMARY KEY, title TEXT, tokens INTEGER)")
            database.executemany("INSERT INTO docs VALUES (?, ?, ?)",
                                 [(1, "Tài liệu cục bộ", 42), (2, "Quoted, dataset", 8)])
        digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        # A dataset directory must not become a Python plugin installation path.
        (data / "plugins").mkdir()
        (data / "plugins/unused.py").write_text("raise RuntimeError('Data must never load plugins')\n")
        for _ in range(2):
            run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
            origin = "http://" + run(*command, "port", "datasette", "8001")
            with httpx.Client(base_url=origin, cookies=cookies, timeout=30) as index_client:
                index = index_client.get("/.json")
                assert index.status_code == 200
                database_path = index.json()["research data"]["path"]
            with httpx.Client(base_url=origin, follow_redirects=True, timeout=30) as anonymous:
                for path in ("/", database_path, database_path + "/docs.json", database_path + "/docs.csv"):
                    assert anonymous.get(path).status_code == 403
            with httpx.Client(base_url=origin, cookies=cookies, follow_redirects=True, timeout=30) as client:
                page = client.get(database_path + "/docs")
                assert page.status_code == 200
                assert "Tài liệu cục bộ" in page.text
                filtered = client.get(database_path + "/docs.json", params={"tokens__gte": 40, "_shape": "objects"})
                assert filtered.status_code == 200
                assert filtered.json()["rows"] == [{"id": 1, "title": "Tài liệu cục bộ", "tokens": 42}]
                query = client.get(database_path + ".json", params={"sql": "select sum(tokens) as total from docs"})
                assert query.status_code == 200
                assert query.json()["rows"] == [[50]]
                exported = client.get(database_path + "/docs.csv")
                assert exported.status_code == 200
                assert '"Quoted, dataset"' in exported.text
                rejected = client.get(database_path + ".json", params={"sql": "delete from docs"})
                assert rejected.status_code == 400
                assert client.get(database_path + ".db").status_code == 403
            assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == digest
            assert not list(data.glob("*-wal"))
            assert not list(data.glob("*-journal"))
    finally:
        run(*command, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
