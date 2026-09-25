"""preserve-active-model parse_dotenv must safely catch TypeError in shlex."""
import tempfile
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from importlib.machinery import SourceFileLoader
pam = SourceFileLoader("preserve_active_model", str(REPO_ROOT / "scripts" / "preserve-active-model.py")).load_module()


class PreserveModelShlexTests(unittest.TestCase):
    def test_malformed_input_safely_ignored(self):
        with tempfile.NamedTemporaryFile("w") as f:
            f.write("KEY='unclosed quote\nVALID_KEY=test\n")
            f.flush()
            parsed = pam.parse_dotenv(Path(f.name))
            self.assertEqual(parsed.get("VALID_KEY"), "test")


if __name__ == "__main__":
    unittest.main()
