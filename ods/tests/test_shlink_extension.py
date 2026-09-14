"""Contract test for Shlink URL shortener extension."""

import json
from pathlib import Path
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/shlink"


def test_shlink_manifest_validity():
    manifest_path = SERVICE / "manifest.yaml"
    assert manifest_path.is_file()
    data = yaml.safe_load(manifest_path.read_text())
    assert data["schema_version"] == "ods.services.v1"
    svc = data["service"]
    assert svc["id"] == "shlink"
    assert svc["port"] == 8080
    assert svc["external_port_default"] == 7856
    assert svc["health"] == "/rest/health"


def test_shlink_catalog_generation(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([
        sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
        "--output", str(output)
    ], check=True)
    catalog = json.loads(output.read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "shlink")
    assert entry["port"] == 8080
    assert entry["external_port_default"] == 7856
    assert entry["health_endpoint"] == "/rest/health"
