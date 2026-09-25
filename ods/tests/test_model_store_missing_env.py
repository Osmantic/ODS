"""model-store-compose-flags must handle missing .env without FileNotFoundError."""
import tempfile
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from importlib.machinery import SourceFileLoader
ms_flags = SourceFileLoader("model_store_compose_flags", str(REPO_ROOT / "scripts" / "model-store-compose-flags.py")).load_module()


class ModelStoreMissingEnvTests(unittest.TestCase):
    def test_missing_env_file_safe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res = ms_flags.resolve_flags(tmpdir, ["-f", "docker-compose.yml"])
            self.assertEqual(res, ["-f", "docker-compose.yml"])


if __name__ == "__main__":
    unittest.main()
