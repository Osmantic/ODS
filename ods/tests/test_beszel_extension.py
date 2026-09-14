"""Contract test for Beszel server monitoring extension."""

import json
from pathlib import Path
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/beszel"


def test_beszel_manifest_validity():
    manifest_path = SERVICE / "manifest.yaml"
    assert manifest_path.is_file()
    data = yaml.safe_load(manifest_path.read_text())
    assert data["schema_version"] == "ods.services.v1"
    svc = data["service"]
    assert svc["id"] == "beszel"
    assert svc["port"] == 8090
    assert svc["external_port_default"] == 7848
    assert svc["health"] == "/api/health"


def test_beszel_catalog_generation(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([
        sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
        "--output", str(output)
    ], check=True)
    catalog = json.loads(output.read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "beszel")
    assert entry["port"] == 8090
    assert entry["external_port_default"] == 7848
    assert entry["health_endpoint"] == "/api/health"
