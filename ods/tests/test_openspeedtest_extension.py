"""Catalog contract and opt-in real transfer endpoints of OpenSpeedTest."""

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
SERVICE = ROOT / "extensions/library/services/openspeedtest"


def test_openspeedtest_catalog_is_discoverable(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
                    "--output", str(output)], check=True)
    generated = json.loads(output.read_text())["extensions"]
    entry = next(item for item in generated if item["id"] == "openspeedtest")
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())["extensions"]
    assert entry == next(item for item in checked if item["id"] == "openspeedtest")
    assert (entry["port"], entry["external_port_default"]) == (3000, 7825)
    assert entry["health_endpoint"] == "/"


@pytest.mark.skipif(os.environ.get("ODS_TEST_OPENSPEEDTEST_DOCKER") != "1",
                    reason="Opt-in Docker transfer endpoint test")
def test_real_download_and_upload_after_recreation(tmp_path):
    name = "ods-speedtest-fixture-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/openspeedtest"
    shutil.copytree(SERVICE, installed)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  openspeedtest:
    container_name: {name}
    ports: !override ["127.0.0.1:0:3000"]
networks:
  ods-network:
    name: {name}
''')
    command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=240)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    run("docker", "network", "create", name)
    try:
        plan = json.loads(run(*command, "config", "--format", "json"))["services"]["openspeedtest"]
        assert len(plan["ports"]) == 1
        assert plan["ports"][0]["host_ip"] == "127.0.0.1"
        assert plan["ports"][0]["target"] == 3000
        for _ in range(2):
            run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
            origin = "http://" + run(*command, "port", "openspeedtest", "3000")
            with httpx.Client(base_url=origin, timeout=60) as client:
                page = client.get("/")
                assert page.status_code == 200
                assert "OpenSpeedTest" in page.text
                download = client.get("/downloading", headers={"Accept-Encoding": "gzip"})
                assert download.status_code == 200
                assert len(download.content) >= 10 * 1024 * 1024
                assert "no-store" in download.headers["cache-control"]
                assert "content-encoding" not in download.headers
                assert "etag" not in download.headers
                uploaded = client.post("/upload", content=os.urandom(2 * 1024 * 1024),
                                       headers={"Content-Type": "application/octet-stream"})
                assert uploaded.status_code == 200
                assert client.get("/missing-test-resource").status_code == 404
    finally:
        run(*command, "down", "--volumes", "--timeout", "10")
        run("docker", "network", "rm", name)
