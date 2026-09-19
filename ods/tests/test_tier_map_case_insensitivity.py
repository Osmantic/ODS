#!/usr/bin/env python3
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "installers/lib/tier-map.sh"

def test_tier_map_case():
    cmd = f'''
source "{SCRIPT}"
m1="$(tier_to_model "cloud")"
m2="$(tier_to_model "CLOUD")"
[[ "$m1" == "$m2" && -n "$m1" ]] || exit 1

m3="$(tier_to_model "arc")"
m4="$(tier_to_model "ARC")"
[[ "$m3" == "$m4" && -n "$m3" ]] || exit 1
echo "OK"
'''
    res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    assert res.returncode == 0, f"Failed: {res.stderr}"
    assert "OK" in res.stdout
    print("test_tier_map_case_insensitivity passed.")

if __name__ == "__main__":
    test_tier_map_case()
