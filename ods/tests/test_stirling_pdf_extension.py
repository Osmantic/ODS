"""Catalog discovery and opt-in real PDF processing with the installed recipe."""

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
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/stirling-pdf"


def test_stirling_catalog_preserves_authentication_and_port_contract(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
                    "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"]
                 if item["id"] == "stirling-pdf")
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())
    assert entry == next(item for item in checked["extensions"] if item["id"] == "stirling-pdf")
    assert (entry["port"], entry["external_port_default"]) == (8080, 7823)
    assert entry["health_endpoint"] == "/api/v1/info/status"
    required = [item for item in entry["env_vars"] if item.get("required")]
    assert len(required) == 1
    assert required[0]["key"] == "STIRLING_PDF_ADMIN_PASSWORD"
    manifest = yaml.safe_load((SERVICE / "manifest.yaml").read_text())
    password = next(item for item in manifest["service"]["env_vars"]
                    if item["key"] == "STIRLING_PDF_ADMIN_PASSWORD")
    assert password["secret"] is True
    compose = yaml.safe_load((SERVICE / "compose.yaml").read_text())["services"]["stirling-pdf"]
    assert "SECURITY_ENABLELOGIN=true" in compose["environment"]
    assert "SYSTEM_ENABLEANALYTICS=false" in compose["environment"]
    assert "@sha256:" in compose["image"]


@pytest.mark.skipif(os.environ.get("ODS_TEST_STIRLING_PDF_DOCKER") != "1",
                    reason="Opt-in Docker PDF processing and account persistence test")
def test_stirling_processes_pdf_and_keeps_accounts_across_recreation(tmp_path):
    from pypdf import PdfReader, PdfWriter

    name = f"ods-stirling-test-{uuid.uuid4().hex[:10]}"
    installed = tmp_path / "extensions/services/stirling-pdf"
    shutil.copytree(SERVICE, installed)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  stirling-pdf:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8080"]
networks:
  ods-network:
    name: {name}
''')
    command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("STIRLING_PDF_")}
    absent = subprocess.run([*command, "config"], env=environment, capture_output=True, text=True, timeout=60)
    assert absent.returncode != 0
    assert "STIRLING_PDF_ADMIN_PASSWORD" in absent.stderr
    password = "Local-PDF-fixture-" + uuid.uuid4().hex
    environment["STIRLING_PDF_ADMIN_PASSWORD"] = password

    def run(*args):
        result = subprocess.run(args, env=environment, capture_output=True, text=True, timeout=780)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    run("docker", "network", "create", name)
    try:
        plan = json.loads(run(*command, "config", "--format", "json"))["services"]["stirling-pdf"]
        assert plan["ports"][0]["host_ip"] == "127.0.0.1"
        assert plan["ports"][0]["target"] == 8080
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=300)
        original = io.BytesIO()
        writer.write(original)

        for recreate in (False, True):
            if recreate:
                # Initial credentials must not replace the persisted account.
                environment["STIRLING_PDF_ADMIN_PASSWORD"] = "Different-initial-value-" + uuid.uuid4().hex
            run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "600")
            origin = "http://" + run(*command, "port", "stirling-pdf", "8080")
            with httpx.Client(base_url=origin, timeout=60) as client:
                assert client.get("/api/v1/info/status").status_code == 200
                files = {"fileInput": ("fixture.pdf", original.getvalue(), "application/pdf")}
                denied = client.post("/api/v1/general/rotate-pdf", files=files, data={"angle": "90"})
                assert denied.status_code in (401, 403)
                login = client.post("/api/v1/auth/login", json={"username": "admin", "password": password})
                assert login.status_code == 200
                token = login.json()["session"]["access_token"]
                rotated = client.post("/api/v1/general/rotate-pdf", files=files, data={"angle": "90"},
                                      headers={"Authorization": f"Bearer {token}"})
                assert rotated.status_code == 200, rotated.text[:300]
                document = PdfReader(io.BytesIO(rotated.content))
                assert len(document.pages) == 1
                assert document.pages[0].rotation == 90
        assert any((tmp_path / "data/stirling-pdf/configs").iterdir())
    finally:
        run(*command, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
