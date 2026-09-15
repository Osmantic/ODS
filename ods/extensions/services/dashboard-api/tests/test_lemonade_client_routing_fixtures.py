import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from lemonade_client import normalize_base_url, _clean_path, classify_status

def test_clean_path():
    assert _clean_path("") == "/api/v1"
    assert _clean_path("v1/custom") == "/v1/custom"
    assert _clean_path("/already/leading") == "/already/leading"

def test_normalize_base_url():
    assert normalize_base_url("http://localhost:13305/api/v1") == "http://localhost:13305"
    assert normalize_base_url("http://localhost:13305/v1") == "http://localhost:13305"
    assert normalize_base_url("http://localhost:13305") == "http://localhost:13305"

def test_classify_status():
    assert classify_status(401) == "auth_rejected"
    assert classify_status(403) == "auth_rejected"
    assert classify_status(404) == "not_found"
    assert classify_status(408) == "timeout"
    assert classify_status(500) == "provider_error"
    assert classify_status(400) == "request_rejected"
