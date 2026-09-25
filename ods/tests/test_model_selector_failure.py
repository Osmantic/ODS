#!/usr/bin/env python3
"""Execute installer selector-status handling without installing any services."""

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_installer_status_handling():
    for relative, end_marker in (
        ("installers/phases/02-detection.sh", '            if [[ "$_pixel_default_selector"'),
        ("installers/macos/install-macos.sh", '            if [[ -n "$_selector_env"'),
    ):
        source = (ROOT / relative).read_text()
        start = source.index("            _selector_status=0\n")
        block = source[start:source.index(end_marker, start)]
        for status in (0, 2, 3):
            script = f"""
set -eu
_run_catalog_selector() {{ return {status}; }}
fake_selector() {{ return {status}; }}
error() {{ printf '%s\\n' "$*"; }}
ai_warn() {{ printf '%s\\n' "$*"; }}
_selector_python=fake_selector
_selector_script=unused
_selector_catalog=unused
SELECTED_TIER=1
LOG_FILE=/dev/null
ODS_LOG_FILE=/dev/null
{block}
printf 'continued:%s\\n' "$_selector_status"
"""
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            if status == 2:
                assert result.returncode == 1, (relative, result)
                assert "refusing an unsafe tier-map fallback" in result.stdout
                assert "continued:" not in result.stdout
            else:
                assert result.returncode == 0, (relative, result)
                assert f"continued:{status}" in result.stdout


if __name__ == "__main__":
    test_installer_status_handling()
    print("Installer selector status tests passed: 6")
