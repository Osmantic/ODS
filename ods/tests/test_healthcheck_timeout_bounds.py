"""Healthcheck helper and CLI must guard non-positive timeout values."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from healthcheck import check_http, check_tcp  # noqa: E402

SCRIPT = ROOT / "scripts/healthcheck.py"


def test_check_tcp_guards_non_positive_timeout():
    ok, detail = check_tcp("127.0.0.1", 80, timeout=0)
    assert ok is False
    assert "timeout must be positive" in detail

    ok, detail = check_tcp("127.0.0.1", 80, timeout=-0.5)
    assert ok is False
    assert "timeout must be positive" in detail


def test_check_http_guards_non_positive_timeout():
    ok, detail, status = check_http(
        "http://127.0.0.1:80",
        method="GET",
        timeout=0,
        allowed_status={200},
        body_regex=None,
        user_agent="test-agent",
    )
    assert ok is False
    assert "timeout must be positive" in detail
    assert status is None

    ok, detail, status = check_http(
        "http://127.0.0.1:80",
        method="GET",
        timeout=-1.0,
        allowed_status={200},
        body_regex=None,
        user_agent="test-agent",
    )
    assert ok is False
    assert "timeout must be positive" in detail
    assert status is None


def test_cli_rejects_non_positive_timeout():
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "127.0.0.1:80", "--timeout", "0", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 2
    assert "must be > 0" in res.stdout

    res2 = subprocess.run(
        [sys.executable, str(SCRIPT), "http://127.0.0.1:80", "--timeout", "-2", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res2.returncode == 2
    assert "must be > 0" in res2.stdout


if __name__ == "__main__":
    test_check_tcp_guards_non_positive_timeout()
    test_check_http_guards_non_positive_timeout()
    test_cli_rejects_non_positive_timeout()
    print("test_healthcheck_timeout_bounds: OK")
