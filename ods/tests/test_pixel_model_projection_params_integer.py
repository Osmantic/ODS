"""Verify projection enforces strict integer output parameters."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_model_contract import projection, ModelError

class ProjectionParamsIntegerTests(unittest.TestCase):
    def test_float_output_param_rejected(self):
        config = {
            "agents": {
                "list": [{"id": "pixel", "model": "ods-local/test", "params": {"maxTokens": 2048.5}}],
                "defaults": {}
            },
            "models": {"providers": {"ods-local": {"models": [{"id": "test", "name": "ODS Local test", "contextWindow": 8192, "maxTokens": 4096, "reasoning": False}]}}},
            "plugins": {"entries": {"pixel-ods": {"enabled": True, "config": {}}}}
        }
        with self.assertRaises(ModelError):
            projection(config)

if __name__ == "__main__":
    unittest.main()
