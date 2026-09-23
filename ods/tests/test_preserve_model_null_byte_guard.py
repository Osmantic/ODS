"""preserve-active-model shell_value must reject null bytes."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from importlib.machinery import SourceFileLoader
pam = SourceFileLoader("preserve_active_model", str(REPO_ROOT / "scripts" / "preserve-active-model.py")).load_module()


class PreserveModelNullByteTests(unittest.TestCase):
    def test_null_byte_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            pam.shell_value("malicious\x00string")
        self.assertIn("null byte in shell value", str(ctx.exception))

    def test_clean_string_accepted(self):
        self.assertEqual(pam.shell_value("clean_value"), '"clean_value"')


if __name__ == "__main__":
    unittest.main()
