"""Offline demo runner must handle EOF gracefully without crashing under set -e."""

import subprocess
from pathlib import Path


def test_demo_offline_exits_cleanly_on_empty_input():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts/demo-offline.sh"

    result = subprocess.run(
        ["bash", str(script)],
        input="",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"
    assert "Select a demo:" in result.stdout


def test_demo_offline_handles_quit():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts/demo-offline.sh"

    result = subprocess.run(
        ["bash", str(script)],
        input="q\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"
    assert "Thanks for watching!" in result.stdout


if __name__ == "__main__":
    test_demo_offline_exits_cleanly_on_empty_input()
    test_demo_offline_handles_quit()
    print("test_demo_offline_eof passed.")
