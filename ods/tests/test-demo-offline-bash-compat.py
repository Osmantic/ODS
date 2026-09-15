#!/usr/bin/env python3
"""Regression test for demo-offline.sh execution across system bash versions."""
from pathlib import Path
import subprocess
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = ROOT_DIR / "scripts" / "demo-offline.sh"


class DemoOfflineBashCompatTests(unittest.TestCase):
    def test_demo_offline_quits_cleanly_under_system_bash(self):
        """demo-offline.sh must handle quit selection without bad substitution errors under /bin/bash."""
        for bash_bin in ("/bin/bash", "bash"):
            with self.subTest(bash=bash_bin):
                res = subprocess.run(
                    [bash_bin, str(DEMO_SCRIPT)],
                    input="q\n",
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertEqual(res.returncode, 0, res.stderr)
                self.assertIn("Thanks for watching!", res.stdout)
                self.assertNotIn("bad substitution", res.stderr)

    def test_demo_offline_handles_uppercase_quit(self):
        """Uppercase 'Q' and 'QUIT' must be folded to lowercase portably."""
        res = subprocess.run(
            ["bash", str(DEMO_SCRIPT)],
            input="QUIT\n",
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Thanks for watching!", res.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
