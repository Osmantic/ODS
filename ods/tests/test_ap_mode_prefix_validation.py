"""Regression test: verify ap-mode.sh validates CIDR prefix range in bring_up_interface."""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ap-mode.sh"


def test_ap_mode_rejects_invalid_cidr_prefixes():
    invalid_prefixes = ["0", "33", "99", "-1", "abc", "24/24"]
    for bad_prefix in invalid_prefixes:
        cmd = f"source {SCRIPT}; ODS_AP_PREFIX={bad_prefix} bring_up_interface"
        res = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode != 0, f"Expected bring_up_interface to fail for prefix {bad_prefix}"
        assert f"invalid CIDR prefix '{bad_prefix}'" in res.stderr


def test_ap_mode_accepts_valid_cidr_prefix():
    # Sourcing and verifying _netmask_to_prefix outputs valid range (1..32)
    valid_cases = [
        ("255.255.255.0", "24"),
        ("255.255.0.0", "16"),
        ("255.0.0.0", "8"),
        ("255.255.255.252", "30"),
    ]
    for mask, expected_prefix in valid_cases:
        cmd = f"source {SCRIPT}; _netmask_to_prefix {mask}"
        res = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0
        assert res.stdout.strip() == expected_prefix


if __name__ == "__main__":
    test_ap_mode_rejects_invalid_cidr_prefixes()
    test_ap_mode_accepts_valid_cidr_prefix()
    print("test_ap_mode_prefix_validation: OK")
