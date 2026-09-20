"""Regression test: verify check_http enforces http and https schemes."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from healthcheck import check_http  # noqa: E402


def test_check_http_rejects_non_http_schemes():
    for invalid_url in [
        "file:///etc/hosts",
        "ftp://example.com/health",
        "gopher://localhost:70",
        "javascript:alert(1)",
        "data:text/plain;base64,SGVsbG8=",
    ]:
        ok, detail, status = check_http(
            invalid_url,
            method="GET",
            timeout=2.0,
            allowed_status={200},
            body_regex=None,
            user_agent="test-agent",
        )
        assert ok is False
        assert "url scheme must be http or https" in detail
        assert status is None


if __name__ == "__main__":
    test_check_http_rejects_non_http_schemes()
    print("test_healthcheck_scheme_guard: OK")
