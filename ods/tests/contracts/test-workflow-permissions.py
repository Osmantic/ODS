#!/usr/bin/env python3
"""Required CI workflows must declare a read-only default token."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = (
    "dashboard.yml",
    "lint-powershell.yml",
    "test-linux.yml",
    "validate-env.yml",
)


def main() -> None:
    expected = "\npermissions:\n  contents: read\n"
    for name in WORKFLOWS:
        path = REPO_ROOT / ".github" / "workflows" / name
        text = path.read_text(encoding="utf-8")
        if expected not in text:
            raise AssertionError(f"{name} must default GITHUB_TOKEN to contents: read")
        print(f"[PASS] {name} uses a read-only default token")


if __name__ == "__main__":
    main()
