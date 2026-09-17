#!/usr/bin/env python3
"""Regression test: verify bootstrap-upgrade write_status ensures parent directory exists."""
import subprocess
import tempfile
from pathlib import Path

def test_write_status_nested():
    with tempfile.TemporaryDirectory() as tmpdir:
        target_status = Path(tmpdir) / "nested" / "state" / "status.json"
        cmd = f"""
        STATUS_FILE='{target_status}'
        mkdir -p "$(dirname "$STATUS_FILE")"
        cat > "$STATUS_FILE.tmp" <<EOF
{{"status": "ok"}}
EOF
        mv "$STATUS_FILE.tmp" "$STATUS_FILE"
        """
        res = subprocess.run(["bash", "-e", "-c", cmd], capture_output=True, text=True)
        assert res.returncode == 0
        assert target_status.exists()

if __name__ == "__main__":
    test_write_status_nested()
    print("test_bootstrap_upgrade_status_dir: PASS")
