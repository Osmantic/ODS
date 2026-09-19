"""Hermes slash worker age must be evaluated numerically to prevent premature termination."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class HermesSlashWorkerAgeTests(unittest.TestCase):
    def test_numeric_coercion_prevents_string_comparison_overkill(self):
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "prune-hermes-slash-workers.sh"
        with tempfile.NamedTemporaryFile("w", delete=False) as fixture:
            # Process table fixture:
            # Worker 101: 90s (should NOT be pruned when max_age=3600)
            # Worker 102: 45s (should NOT be pruned when max_age=3600)
            # Worker 103: 4000s (SHOULD be pruned)
            fixture.write("101\t90\t101 90 python3 -m tui_gateway.slash_worker\n")
            fixture.write("102\t45\t102 45 python3 -m tui_gateway.slash_worker\n")
            fixture.write("103\t4000\t103 4000 python3 -m tui_gateway.slash_worker\n")
            fixture_name = fixture.name

        try:
            env = os.environ.copy()
            env["ODS_HERMES_SLASH_WORKER_PS_FIXTURE"] = fixture_name
            env["HERMES_SLASH_WORKER_MAX_COUNT"] = "10"
            env["HERMES_SLASH_WORKER_MAX_AGE_SECONDS"] = "3600"

            res = subprocess.run(
                ["bash", str(script_path), "--dry-run"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("1 slash worker(s) selected for pruning", res.stdout)
            self.assertIn("pid=103 age=4000s", res.stdout)
            self.assertNotIn("pid=101", res.stdout)
            self.assertNotIn("pid=102", res.stdout)
        finally:
            os.unlink(fixture_name)


if __name__ == "__main__":
    unittest.main()
