"""healthcheck must validate that retries is an integer within [0, 50]."""
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "healthcheck.py"


class HealthcheckRetriesTests(unittest.TestCase):
    def test_out_of_bound_retries_rejected(self):
        for bad in ("-1", "51", "100"):
            res = subprocess.run(["python3", str(SCRIPT), "--retries", bad, "http://localhost:8080"], capture_output=True, text=True)
            self.assertEqual(res.returncode, 2)
            self.assertIn("out of range", res.stdout)


if __name__ == "__main__":
    unittest.main()
