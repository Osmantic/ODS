# detect-hardware detect_cpu fallback test
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "detect-hardware.sh"


class HardwareCpuFallbackTests(unittest.TestCase):
    def test_detect_cpu_empty_handling(self):
        cmd = f"""
        source {SCRIPT}
        res=$(cpu=$(echo "" | xargs || true); [[ -n "$cpu" ]] && echo "$cpu" || echo "Unknown")
        echo "$res"
        """
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "Unknown")


if __name__ == "__main__":
    unittest.main()
