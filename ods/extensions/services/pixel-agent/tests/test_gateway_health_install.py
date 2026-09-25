"""Exercise installer gateway health discovery without installed services."""
import os
from pathlib import Path
import subprocess

import pytest

SOURCE = (Path(__file__).resolve().parents[4] / "installers/lib/pixel-host-install.sh").read_text()
FUNCTIONS = SOURCE[SOURCE.index("_ods_pixel_gateway_health() {"):SOURCE.index("_ods_pixel_wait_http() {")]
HARNESS = r'''
curl() {
    printf '%s\n' "$*" >>"$CALLS"
    case "$MODE" in
        down) return 7 ;;
        ipv4) [[ "${*: -1}" == *127.0.0.1* ]] || return 7 ;;
        stale) [[ "${*: -1}" == *127.0.0.1* ]] || { printf '%s' '{"ok":true,"status":"starting"}'; return 0; } ;;
    esac
    printf '%s' '{"ok":true,"status":"live"}'
}
jq() { local body; IFS= read -r body; [[ "$body" == '{"ok":true,"status":"live"}' ]]; }
sleep() { printf 'sleep %s\n' "$*" >>"$CALLS"; }
ai_bad() { printf '%s\n' "$*"; }
_ods_pixel_wait_gateway 2
'''


@pytest.mark.parametrize("mode,hosts", [
    ("dual", ["[::1]"]), ("ipv4", ["[::1]", "127.0.0.1"]),
    ("stale", ["[::1]", "127.0.0.1"]),
])
def test_gateway_health_prefers_ipv6_and_falls_back_only_for_readiness(tmp_path, mode, hosts):
    calls = tmp_path / "calls"
    result = subprocess.run(["bash", "-c", FUNCTIONS + HARNESS],
        env={**os.environ, "MODE":mode, "CALLS":str(calls)}, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert calls.read_text().splitlines() == [
        f"--noproxy * --fail --silent --show-error --max-time 2.5 http://{host}:18789/health" for host in hosts]


def test_gateway_health_exhausts_bounded_attempts_and_reports_unavailable(tmp_path):
    calls = tmp_path / "calls"
    result = subprocess.run(["bash", "-c", FUNCTIONS + HARNESS],
        env={**os.environ, "MODE":"down", "CALLS":str(calls)}, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1
    assert "did not become ready" in result.stdout
    assert len(calls.read_text().splitlines()) == 5  # Four GETs and one inter-attempt wait.
