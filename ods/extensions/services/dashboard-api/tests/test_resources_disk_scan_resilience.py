import pytest
from pathlib import Path
from unittest.mock import patch
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from routers.resources import _scan_service_disk

def test_scan_service_disk_handles_permission_error(tmp_path, monkeypatch):
    monkeypatch.setattr("routers.resources.DATA_DIR", str(tmp_path))
    
    with patch.object(Path, "iterdir", side_effect=PermissionError("Permission denied")):
        result = _scan_service_disk()
        assert result == {}

def test_scan_service_disk_handles_nonexistent_dir(monkeypatch):
    monkeypatch.setattr("routers.resources.DATA_DIR", "/nonexistent/data/dir")
    result = _scan_service_disk()
    assert result == {}
