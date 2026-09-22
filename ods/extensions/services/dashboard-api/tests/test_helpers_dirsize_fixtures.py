import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from helpers import dir_size_gb, invalidate_dir_size_cache, clear_dir_size_cache, is_plausible_single_request_tps

def test_dir_size_gb_and_cache(tmp_path):
    test_file = tmp_path / "sample.bin"
    test_file.write_bytes(b"A" * 1024 * 1024)
    size1 = dir_size_gb(tmp_path)
    assert isinstance(size1, float)
    assert size1 >= 0.0
    size2 = dir_size_gb(tmp_path)
    assert size1 == size2
    invalidate_dir_size_cache(tmp_path)
    clear_dir_size_cache()

def test_is_plausible_single_request_tps():
    assert is_plausible_single_request_tps(10.5) is True
    assert is_plausible_single_request_tps(0.0) is False
    assert is_plausible_single_request_tps(-5.0) is False
    assert is_plausible_single_request_tps(50000.0) is False
    assert is_plausible_single_request_tps("not-a-number") is False
