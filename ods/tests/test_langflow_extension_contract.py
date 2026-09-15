import pytest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "extensions/library/services/langflow"

def test_langflow_manifest_structure():
    manifest_path = SERVICE_DIR / "manifest.yaml"
    assert manifest_path.exists(), f"Manifest missing for langflow"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(manifest, dict)
    assert "name" in manifest or "service" in manifest
    svc = manifest.get("service", manifest)
    if "port" in svc:
        assert isinstance(svc["port"], int)

def test_langflow_compose_validity():
    compose_path = SERVICE_DIR / "compose.yaml"
    if not compose_path.exists():
        compose_path = SERVICE_DIR / "compose.yaml.disabled"
    assert compose_path.exists(), f"Compose missing for langflow"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert "services" in compose
    assert len(compose["services"]) > 0
