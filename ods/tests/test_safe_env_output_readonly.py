"""load_env_from_output must protect against readonly variable export."""
import subprocess
import unittest
from pathlib import Path

class SafeEnvOutputReadonlyTests(unittest.TestCase):
    def test_output_containing_euid_skipped(self):
        script = Path(__file__).resolve().parents[1] / "lib" / "safe-env.sh"
        cmd = f"""
        set -euo pipefail
        source {script}
        echo 'EUID="1000"' | load_env_from_output
        echo "OK"
        """
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("OK", res.stdout)

if __name__ == "__main__":
    unittest.main()
