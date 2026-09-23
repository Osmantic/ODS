"""sync-manifest-schema must reject manifests missing contracts.extensions section."""
import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from importlib.machinery import SourceFileLoader
sms = SourceFileLoader("sync_manifest_schema", str(REPO_ROOT / "scripts" / "sync-manifest-schema.py")).load_module()


class SyncManifestContractsTests(unittest.TestCase):
    def test_missing_contracts_raises_value_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
            f.write(json.dumps({"name": "test"}))
            f.flush()
            with patch.object(sms, "MANIFEST_FILE", Path(f.name)):
                with self.assertRaises(ValueError) as ctx:
                    sms.canonical_schema_path()
                self.assertIn("missing contracts.extensions", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
