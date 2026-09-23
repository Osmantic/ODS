"""validate-generated-configs resolve_json_path must reject empty dotted paths."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from importlib.machinery import SourceFileLoader
vgc = SourceFileLoader("validate_generated_configs", str(REPO_ROOT / "scripts" / "validate-generated-configs.py")).load_module()


class ResolveJsonPathTests(unittest.TestCase):
    def test_empty_dotted_path_rejected(self):
        for bad in ("", "   ", None):
            with self.assertRaises(KeyError) as ctx:
                vgc.resolve_json_path({"a": 1}, bad)
            self.assertIn("dotted_path must be a non-empty string", str(ctx.exception))

    def test_valid_dotted_path_resolves(self):
        data = {"server": {"port": 8080}}
        self.assertEqual(vgc.resolve_json_path(data, "server.port"), 8080)


if __name__ == "__main__":
    unittest.main()
