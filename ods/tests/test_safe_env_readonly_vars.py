"""safe-env must skip readonly variables without crashing caller under set -e."""
import subprocess
import tempfile
import unittest
from pathlib import Path

class SafeEnvReadonlyTests(unittest.TestCase):
    def test_readonly_variables_skipped_without_exit(self):
        script = Path(__file__).resolve().parents[1] / "lib" / "safe-env.sh"
        with tempfile.NamedTemporaryFile("w") as env_f:
            env_f.write("EUID=1001\nPPID=9999\nGROUPS=1000\nAPP_PORT=8080\n")
            env_f.flush()
            cmd = f"""
            set -euo pipefail
            source {script}
            load_env_file {env_f.name}
            echo "PORT=$APP_PORT"
            """
            res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("PORT=8080", res.stdout)

if __name__ == "__main__":
    unittest.main()
