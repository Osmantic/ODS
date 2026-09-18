"""Verify binding resolution rejects non-dict plugin entries."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_model_contract import binding, ModelError

class BindingPluginTypeTests(unittest.TestCase):
    def test_non_dict_plugin_rejected(self):
        config = {
            "agents": {"list": [{"id": "pixel", "model": "ods-local/test"}]},
            "models": {"providers": {"ods-local": {"models": [{"id": "test", "name": "ODS Local test"}]}}},
            "plugins": {"entries": {"pixel-ods": True}}
        }
        with self.assertRaises(ModelError):
            binding(config)

if __name__ == "__main__":
    unittest.main()
