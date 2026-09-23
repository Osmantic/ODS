"""model-store-compose-flags must validate that install_dir exists and is a directory."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from importlib.machinery import SourceFileLoader
ms_flags = SourceFileLoader("model_store_compose_flags", str(REPO_ROOT / "scripts" / "model-store-compose-flags.py")).load_module()


class ModelStoreInstallDirTests(unittest.TestCase):
    def test_nonexistent_install_dir_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            ms_flags.resolve_flags("/nonexistent/ods/install/path", ["-f", "base.yml"])
        self.assertIn("install_dir does not exist", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
