"""Contract test for Gotify push notification extension."""

import json
from pathlib import Path
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/gotify"


def test_gotify_manifest_validity():
    manifest_path = SERVICE / "manifest.yaml"
    assert manifest_path.is_file()
    data = yaml.safe_load(manifest_path.read_text())
    assert data["schema_version"] == "ods.services.v1"
    svc = data["service"]
    assert svc["id"] == "gotify"
    assert svc["port"] == 80
    assert svc["external_port_default"] == 7843
    assert svc["health"] == "/version"


def test_gotify_catalog_generation(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([
        sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
        "--output", str(output)
    ], check=True)
    catalog = json.loads(output.read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "gotify")
    assert entry["port"] == 80
    assert entry["external_port_default"] == 7843
    assert entry["health_endpoint"] == "/version"
