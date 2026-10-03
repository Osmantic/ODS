"""Run the real readiness reporter against an intentionally stalled Docker CLI."""

import os
import signal
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("code", ["200", "302", "401", "403"])
def test_http_ready_service_does_not_wait_for_docker(tmp_path, code):
    marker = tmp_path / "inspected"
    script = r'''
set -euo pipefail
source "$SUMMARY_LIB"
curl() { printf '%s' "$HTTP_CODE"; }
docker() { printf called > "$INSPECT_MARKER"; sleep 30; }
export -f docker
printf 'Dashboard|http://fixture.test/health|ods-dashboard|http://fixture.test\n' |
  ods_readiness_summary 'ods status' '' 'http://fixture.test'
'''
    process = subprocess.Popen(
        ["bash", "-c", script], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True,
        env={**os.environ, "SUMMARY_LIB": str(ROOT / "installers/lib/readiness-summary.sh"),
             "HTTP_CODE": code, "INSPECT_MARKER": str(marker), "DOCKER_CMD": "docker"},
    )
    try:
        output, error = process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, error = process.communicate()
        pytest.fail("HTTP-ready summary blocked on redundant Docker inspection")
    assert process.returncode == 0, error
    assert "Ready now: 1/1" in output
    assert f"HTTP {code}" in output
    assert not marker.exists()


@pytest.mark.parametrize("state,expected", [
    ("running", "starting - HTTP 000"),
    ("unhealthy", "needs attention - container unhealthy, HTTP 000"),
    ("missing", "not detected - missing"),
])
def test_unready_service_keeps_container_diagnostics(tmp_path, state, expected):
    marker = tmp_path / "inspected"
    result = subprocess.run(
        ["bash", "-c", r'''
set -euo pipefail
source "$SUMMARY_LIB"
curl() { printf 000; }
docker() { printf called > "$INSPECT_MARKER"; printf '%s' "$CONTAINER_STATE"; }
printf 'Dashboard|http://fixture.test/health|ods-dashboard|http://fixture.test\n' |
  ods_readiness_summary
'''], text=True, capture_output=True, timeout=5, check=False,
        env={**os.environ, "SUMMARY_LIB": str(ROOT / "installers/lib/readiness-summary.sh"),
             "INSPECT_MARKER": str(marker), "CONTAINER_STATE": state, "DOCKER_CMD": "docker"},
    )
    assert result.returncode == 0, result.stderr
    assert "Ready now: 0/1" in result.stdout
    assert expected in result.stdout
    assert marker.read_text() == "called"
