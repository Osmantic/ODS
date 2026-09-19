import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from portal_identity_contract import normalize_device_hostname

def test_normalize_valid_hostname():
    assert normalize_device_hostname("my-device") == "my-device"

def test_normalize_mixed_case_and_symbols():
    assert normalize_device_hostname("My_MacBook_Pro!") == "my-macbook-pro"

def test_normalize_empty_or_whitespace():
    assert normalize_device_hostname("") == "ods"
    assert normalize_device_hostname("   ") == "ods"
    assert normalize_device_hostname(None) == "ods"

def test_normalize_max_length_clamping():
    long_name = "a" * 80
    normalized = normalize_device_hostname(long_name)
    assert len(normalized) == 63
