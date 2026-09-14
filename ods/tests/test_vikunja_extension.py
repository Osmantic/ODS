"""Contract test for Vikunja task management extension."""

import json
from pathlib import Path
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/vikunja"


def test_vikunja_manifest_validity():
    manifest_path = SERVICE / "manifest.yaml"
    assert manifest_path.is_file()
    data = yaml.safe_load(manifest_path.read_text())
    assert data["schema_version"] == "ods.services.v1"
    svc = data["service"]
    assert svc["id"] == "vikunja"
    assert svc["port"] == 3456
    assert svc["external_port_default"] == 7855
    assert svc["health"] == "/api/v1/info"


def test_vikunja_catalog_generation(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([
        sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
        "--output", str(output)
    ], check=True)
    catalog = json.loads(output.read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "vikunja")
    assert entry["port"] == 3456
    assert entry["external_port_default"] == 7855
    assert entry["health_endpoint"] == "/api/v1/info"
