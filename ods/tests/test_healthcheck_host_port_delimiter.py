"""Regression test: verify _parse_host_port guards missing port delimiter and strips whitespace."""

import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from healthcheck import _parse_host_port  # noqa: E402


def test_parse_host_port_valid():
    host, port = _parse_host_port("127.0.0.1:8080")
    assert host == "127.0.0.1"
    assert port == 8080

    host, port = _parse_host_port("  localhost:9099  ")
    assert host == "localhost"
    assert port == 9099

    host, port = _parse_host_port("[::1]:8000")
    assert host == "::1"
    assert port == 8000


def test_parse_host_port_missing_delimiter():
    for invalid in ["localhost", "127.0.0.1", "   "]:
        with pytest.raises(ValueError) as exc:
            _parse_host_port(invalid)
        assert "missing port delimiter" in str(exc.value)


if __name__ == "__main__":
    test_parse_host_port_valid()
    test_parse_host_port_missing_delimiter()
    print("test_healthcheck_host_port_delimiter: OK")
