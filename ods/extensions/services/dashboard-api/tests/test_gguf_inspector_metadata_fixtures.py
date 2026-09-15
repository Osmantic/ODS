import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from gguf_inspector import inspect_gguf

def test_truncated_gguf_returns_unreadable(tmp_path):
    short_file = tmp_path / "truncated.gguf"
    short_file.write_bytes(b"GGUF\x03\x00\x00\x00")
    res = inspect_gguf(short_file)
    assert res["readable"] is False
    assert res["architecture"] == "unknown"

def test_nonexistent_gguf_returns_not_found(tmp_path):
    missing_file = tmp_path / "nonexistent.gguf"
    res = inspect_gguf(missing_file)
    assert res["readable"] is False
    assert res["exists"] is False

def test_non_gguf_binary(tmp_path):
    fake_file = tmp_path / "invalid.bin"
    fake_file.write_bytes(b"NOT_A_GGUF_HEADER_AT_ALL_1234567890")
    res = inspect_gguf(fake_file)
    assert res["readable"] is False
    assert res["error"] == "not a GGUF file"
