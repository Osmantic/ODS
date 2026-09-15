#!/usr/bin/env python3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPT = ROOT_DIR / "ods/lib/validate-dependencies.sh"

def test_bash32_compatibility():
    # Test script running in /bin/bash (Bash 3.2 on macOS)
    bash_script = f'''
source "{SCRIPT}"
SERVICE_IDS=("svc_a" "svc_b")
SERVICE_DEPENDS[svc_b]="svc_a"
enabled_services=" svc_a svc_b "
# Test function existence
type validate_service_dependencies >/dev/null 2>&1 || exit 1
echo "OK"
'''
    res = subprocess.run(["/bin/bash", "-c", bash_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Failed: {res.stderr}"
    assert "OK" in res.stdout
    print("test_validate_dependencies_bash32 passed.")

if __name__ == "__main__":
    test_bash32_compatibility()
