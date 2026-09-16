#!/usr/bin/env python3
"""Regression test for preflight memory and disk thresholds on T-prefixed tiers."""
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "preflight-engine.sh"


def run_preflight(tier: str, ram_gb: int, disk_gb: int, report_path: Path) -> dict[str, str]:
    cmd = [
        "bash",
        str(SCRIPT),
        "--tier",
        tier,
        "--ram-gb",
        str(ram_gb),
        "--disk-gb",
        str(disk_gb),
        "--report",
        str(report_path),
        "--env",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    env_vars = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip().strip('"')
    return env_vars


def test_preflight_enforces_t_prefixed_tier_thresholds():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        report = tmp_root / "report.json"

        # Tier T4 requires 64GB RAM and 150GB disk. 16GB RAM / 50GB disk must be blocked.
        result_t4_low = run_preflight("T4", 16, 50, report)
        assert result_t4_low.get("PREFLIGHT_BLOCKERS") != "0", (
            f"Expected T4 with 16GB RAM to have blockers, got {result_t4_low}"
        )
        assert result_t4_low.get("PREFLIGHT_CAN_PROCEED") == "false", (
            f"Expected T4 with 16GB RAM to fail proceed, got {result_t4_low}"
        )

        # Tier T3 requires 48GB RAM and 80GB disk. 16GB RAM / 50GB disk must be blocked.
        result_t3_low = run_preflight("T3", 16, 50, report)
        assert result_t3_low.get("PREFLIGHT_BLOCKERS") != "0", (
            f"Expected T3 with 16GB RAM to have blockers, got {result_t3_low}"
        )
        assert result_t3_low.get("PREFLIGHT_CAN_PROCEED") == "false", (
            f"Expected T3 with 16GB RAM to fail proceed, got {result_t3_low}"
        )

        # Tier T4 with full resources (64GB RAM, 150GB disk) should pass without blockers.
        result_t4_ok = run_preflight("T4", 64, 150, report)
        assert result_t4_ok.get("PREFLIGHT_BLOCKERS") == "0", (
            f"Expected T4 with 64GB/150GB to have 0 blockers, got {result_t4_ok}"
        )
        assert result_t4_ok.get("PREFLIGHT_CAN_PROCEED") == "true", (
            f"Expected T4 with 64GB/150GB to pass proceed, got {result_t4_ok}"
        )


if __name__ == "__main__":
    test_preflight_enforces_t_prefixed_tier_thresholds()
    print("[PASS] test_preflight_enforces_t_prefixed_tier_thresholds")
