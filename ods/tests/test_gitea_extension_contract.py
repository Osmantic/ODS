import pytest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "extensions/library/services/gitea"

def test_gitea_manifest_structure():
    manifest_path = SERVICE_DIR / "manifest.yaml"
    assert manifest_path.exists(), f"Manifest missing for gitea"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(manifest, dict)
    assert "name" in manifest or "service" in manifest
    svc = manifest.get("service", manifest)
    if "port" in svc:
        assert isinstance(svc["port"], int)

def test_gitea_compose_validity():
    compose_path = SERVICE_DIR / "compose.yaml"
    if not compose_path.exists():
        compose_path = SERVICE_DIR / "compose.yaml.disabled"
    assert compose_path.exists(), f"Compose missing for gitea"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert "services" in compose
    assert len(compose["services"]) > 0
