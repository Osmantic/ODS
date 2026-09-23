"""resolve-compose-stack must normalize tier arguments to uppercase."""
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "resolve-compose-stack.sh"


class ComposeTierCaseTests(unittest.TestCase):
    def test_lowercase_tier_accepted(self):
        res = subprocess.run(["bash", str(SCRIPT), "--tier", "cloud"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)


if __name__ == "__main__":
    unittest.main()
