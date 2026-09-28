"""Regression test for Issue #5648:
AMD topology JSON VRAM parsing must use LC_ALL=C under decimal-comma locales.
"""

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "installers" / "lib" / "amd-topo.sh"


def test_amd_topo_script_scopes_vram_awk_to_c_locale():
    content = SCRIPT.read_text(encoding="utf-8")
    assert "LC_ALL=C awk -v bytes=\"$vram_bytes\" 'BEGIN { printf \"%.1f\"" in content


def test_vram_formatting_under_comma_locale():
    bytes_val = 25769803776  # 24 GB
    cmd = (
        f"bytes={bytes_val}; "
        "vram_gb=$(LC_ALL=C awk -v bytes=\"$bytes\" 'BEGIN { printf \"%.1f\", bytes / 1073741824 }'); "
        "printf '0\\tCard\\t%s\\n' \"$vram_gb\" | jq -Rn '[inputs | split(\"\\t\") | {memory_gb: (.[2] | tonumber)}]'"
    )
    env = os.environ.copy()
    env["LC_ALL"] = "de_DE.UTF-8"
    env["LANG"] = "de_DE.UTF-8"

    res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env, check=False)
    assert res.returncode == 0, f"Command failed under comma locale: {res.stderr}"
    data = json.loads(res.stdout)
    assert len(data) == 1
    assert data[0]["memory_gb"] == 24.0


if __name__ == "__main__":
    test_amd_topo_script_scopes_vram_awk_to_c_locale()
    test_vram_formatting_under_comma_locale()
    print("test_amd_topo_vram_locale: OK")
