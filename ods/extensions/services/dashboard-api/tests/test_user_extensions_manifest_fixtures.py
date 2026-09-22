import pytest
import yaml
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from user_extensions import scan_user_extension_services

def test_scan_user_extension_valid(tmp_path):
    ext_dir = tmp_path / "custom-service"
    ext_dir.mkdir()
    (ext_dir / "compose.yaml").write_text("services: {}", encoding="utf-8")
    manifest = {
        "service": {
            "port": 9000,
            "external_port_default": 9001,
            "health": "/health",
            "name": "Custom Service",
        }
    }
    (ext_dir / "manifest.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
    services = scan_user_extension_services(tmp_path)
    assert "custom-service" in services
    assert services["custom-service"]["port"] == 9000
    assert services["custom-service"]["health"] == "/health"

def test_scan_user_extension_rejects_invalid_health(tmp_path):
    ext_dir = tmp_path / "bad-health-service"
    ext_dir.mkdir()
    (ext_dir / "compose.yaml").write_text("services: {}", encoding="utf-8")
    manifest = {
        "service": {
            "port": 9000,
            "health": "http://malicious.com/attack",
        }
    }
    (ext_dir / "manifest.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
    services = scan_user_extension_services(tmp_path)
    assert "bad-health-service" not in services
