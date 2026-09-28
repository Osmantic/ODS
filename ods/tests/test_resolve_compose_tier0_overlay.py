"""Regression test for Issue #5466:
resolve-compose-stack.sh must include docker-compose.tier0.yml for Tier 0 installs.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "resolve-compose-stack.sh"


def test_tier0_numeric_layers_tier0_overlay():
    res = subprocess.run(
        ["bash", str(SCRIPT), "--tier", "0", "--gpu-backend", "cpu"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Resolver failed: {res.stderr}"
    assert "-f docker-compose.tier0.yml" in res.stdout


def test_tier0_alpha_layers_tier0_overlay():
    res = subprocess.run(
        ["bash", str(SCRIPT), "--tier", "T0", "--gpu-backend", "cpu"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Resolver failed: {res.stderr}"
    assert "-f docker-compose.tier0.yml" in res.stdout


def test_tier1_omits_tier0_overlay():
    res = subprocess.run(
        ["bash", str(SCRIPT), "--tier", "1", "--gpu-backend", "cpu"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Resolver failed: {res.stderr}"
    assert "docker-compose.tier0.yml" not in res.stdout


if __name__ == "__main__":
    test_tier0_numeric_layers_tier0_overlay()
    test_tier0_alpha_layers_tier0_overlay()
    test_tier1_omits_tier0_overlay()
    print("test_resolve_compose_tier0_overlay: OK")
