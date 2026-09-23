"""validate-generated-configs load_json must raise FileNotFoundError with descriptive message."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from importlib.machinery import SourceFileLoader
vgc = SourceFileLoader("validate_generated_configs", str(REPO_ROOT / "scripts" / "validate-generated-configs.py")).load_module()


class LoadJsonTests(unittest.TestCase):
    def test_missing_contract_file_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            vgc.load_json(Path("/nonexistent/contract.json"))
        self.assertIn("contract file not found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
