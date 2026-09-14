"""Real Chroma HTTP vectors must survive recreation and a cold legacy-data copy."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/chromadb"
COLLECTIONS = "/api/v2/tenants/default_tenant/databases/default_database/collections"
pytestmark = pytest.mark.skipif(os.environ.get("ODS_TEST_CHROMADB_PERSISTENCE") != "1",
                                reason="Opt-in exact-image vector persistence test")


@pytest.mark.parametrize("legacy", [False, True], ids=["fresh", "legacy-cold-copy"])
def test_vectors_survive_recreation(tmp_path, legacy):
    name = "ods-chroma-persist-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/chromadb"
    shutil.copytree(SERVICE, installed)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  chromadb:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8000"]
networks:
  ods-network:
    name: {name}
''')
    command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    legacy_overlay = tmp_path / "legacy.yaml"
    legacy_overlay.write_text('''services:
  chromadb:
    volumes: !override ["./data/chromadb:/chromadb"]
''')
    initial = [*command, "-f", str(legacy_overlay)] if legacy else command
    run("docker", "network", "create", name)
    try:
        run(*initial, "up", "-d", "--wait", "--wait-timeout", "180")
        origin = "http://" + run(*initial, "port", "chromadb", "8000")
        with httpx.Client(base_url=origin, timeout=30) as client:
            created = client.post(COLLECTIONS, json={"name": "retained-vectors"})
            assert created.status_code == 200, created.text
            collection = created.json()["id"]
            inserted = client.post(f"{COLLECTIONS}/{collection}/add", json={
                "ids": ["record-1"], "embeddings": [[1.0, 0.0, 0.0]],
                "documents": ["Persistent local evidence"], "metadatas": [{"source": "fixture"}]})
            assert inserted.status_code == 201, inserted.text
        run(*initial, "stop", "chromadb")
        if legacy:
            # Never copy a running SQLite/HNSW database. Keep the entire directory.
            destination = tmp_path / "data/chromadb"
            assert not list(destination.iterdir())
            run("docker", "cp", f"{name}:/data/.", str(destination))
        run(*initial, "rm", "-f", "chromadb")
        run(*command, "up", "-d", "--wait", "--wait-timeout", "180")
        origin = "http://" + run(*command, "port", "chromadb", "8000")
        with httpx.Client(base_url=origin, timeout=30) as client:
            retained = client.get(f"{COLLECTIONS}/retained-vectors")
            assert retained.status_code == 200, retained.text
            assert retained.json()["id"] == collection
            query = client.post(f"{COLLECTIONS}/{collection}/query", json={
                "query_embeddings": [[1.0, 0.0, 0.0]], "n_results": 1,
                "include": ["documents", "metadatas", "distances"]})
            assert query.status_code == 200, query.text
            assert query.json()["ids"] == [["record-1"]]
            assert query.json()["documents"] == [["Persistent local evidence"]]
            assert query.json()["metadatas"] == [[{"source": "fixture"}]]
            assert query.json()["distances"] == [[0.0]]
        assert (tmp_path / "data/chromadb/chroma.sqlite3").is_file()
        plan = json.loads(run(*command, "config", "--format", "json"))["services"]["chromadb"]
        assert plan["ports"][0]["host_ip"] == "127.0.0.1"
    finally:
        run(*command, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
