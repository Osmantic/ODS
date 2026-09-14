"""Contract test for Glances monitoring extension."""

import json
from pathlib import Path
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/glances"


def test_glances_manifest_validity():
    manifest_path = SERVICE / "manifest.yaml"
    assert manifest_path.is_file()
    data = yaml.safe_load(manifest_path.read_text())
    assert data["schema_version"] == "ods.services.v1"
    svc = data["service"]
    assert svc["id"] == "glances"
    assert svc["port"] == 61208
    assert svc["external_port_default"] == 7854
    assert svc["health"] == "/api/3/quicklook"


def test_glances_catalog_generation(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([
        sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
        "--output", str(output)
    ], check=True)
    catalog = json.loads(output.read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "glances")
    assert entry["port"] == 61208
    assert entry["external_port_default"] == 7854
    assert entry["health_endpoint"] == "/api/3/quicklook"
