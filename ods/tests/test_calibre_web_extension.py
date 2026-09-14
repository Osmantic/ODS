"""Contract test for Calibre-Web eBook library extension."""

import json
from pathlib import Path
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/calibre-web"


def test_calibre_web_manifest_validity():
    manifest_path = SERVICE / "manifest.yaml"
    assert manifest_path.is_file()
    data = yaml.safe_load(manifest_path.read_text())
    assert data["schema_version"] == "ods.services.v1"
    svc = data["service"]
    assert svc["id"] == "calibre-web"
    assert svc["port"] == 8083
    assert svc["external_port_default"] == 7852
    assert svc["health"] == "/"


def test_calibre_web_catalog_generation(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([
        sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
        "--output", str(output)
    ], check=True)
    catalog = json.loads(output.read_text())
    entry = next(item for item in catalog["extensions"] if item["id"] == "calibre-web")
    assert entry["port"] == 8083
    assert entry["external_port_default"] == 7852
    assert entry["health_endpoint"] == "/"
