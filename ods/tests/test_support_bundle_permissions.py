#!/usr/bin/env python3
"""Regression test: verify ods-support-bundle.sh secures archive permissions to 0600."""
import stat
import subprocess
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
script = repo_root / "scripts" / "ods-support-bundle.sh"

def test_support_bundle_mode():
    with tempfile.TemporaryDirectory() as tmpdir:
        res = subprocess.run(
            ["bash", str(script), "--no-logs", "--output", str(tmpdir)],
            capture_output=True, text=True
        )
        assert res.returncode == 0, f"Expected 0, got {res.returncode}: {res.stderr}"
        archives = list(Path(tmpdir).glob("*.tar.gz"))
        assert len(archives) == 1, "Expected 1 support bundle archive"
        mode = stat.S_IMODE(archives[0].stat().st_mode)
        assert mode == 0o600, f"Expected mode 0600, got {oct(mode)}"

if __name__ == "__main__":
    test_support_bundle_mode()
    print("test_support_bundle_permissions: PASS")
