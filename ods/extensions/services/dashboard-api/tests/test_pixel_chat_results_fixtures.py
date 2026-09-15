import pytest
from pathlib import Path
import sys
import hashlib

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from pixel_chat_results import owner_namespace, ResultConflict, ResultCapacity

def test_owner_namespace_deterministic():
    cred = "test-token-12345"
    expected = hashlib.sha256(cred.encode("utf-8")).hexdigest()
    assert owner_namespace(cred) == expected
    assert len(owner_namespace(cred)) == 64

def test_owner_namespace_distinct_credentials():
    assert owner_namespace("token-a") != owner_namespace("token-b")

def test_exception_types():
    with pytest.raises(ResultConflict):
        raise ResultConflict("conflict")
    with pytest.raises(ResultCapacity):
        raise ResultCapacity("capacity")
