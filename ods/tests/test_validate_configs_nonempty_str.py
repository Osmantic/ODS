"""validate-generated-configs nonempty_string must reject non-strings and whitespace."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from importlib.machinery import SourceFileLoader
vgc = SourceFileLoader("validate_generated_configs", str(REPO_ROOT / "scripts" / "validate-generated-configs.py")).load_module()


class NonemptyStringTests(unittest.TestCase):
    def test_non_strings_and_empty_rejected(self):
        for bad in ("", "   ", "\t\n", None, 123, [], {}):
            self.assertFalse(vgc.nonempty_string(bad))

    def test_valid_strings_accepted(self):
        self.assertTrue(vgc.nonempty_string("valid"))
        self.assertTrue(vgc.nonempty_string("  valid  "))


if __name__ == "__main__":
    unittest.main()
