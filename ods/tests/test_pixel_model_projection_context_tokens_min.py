"""Verify projection enforces contextTokens >= 4096 in resolved limits."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_model_contract import projection, ModelError

class ProjectionContextTokensMinTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "agents": {
                "list": [{"id": "pixel", "model": "ods-local/qwen2.5", "contextTokens": 2048}],
                "defaults": {}
            },
            "models": {
                "providers": {
                    "ods-local": {
                        "models": [{"id": "qwen2.5", "name": "ODS Local qwen2.5", "contextWindow": 8192, "maxTokens": 2048, "reasoning": False}]
                    }
                }
            },
            "plugins": {
                "entries": {
                    "pixel-ods": {"enabled": True, "config": {}}
                }
            }
        }

    def test_undersized_context_tokens_rejected(self):
        with self.assertRaises(ModelError):
            projection(self.config)

    def test_valid_context_tokens_accepted(self):
        self.config["agents"]["list"][0]["contextTokens"] = 8192
        res = projection(self.config)
        self.assertEqual(res["limits"]["contextTokens"], 8192)

if __name__ == "__main__":
    unittest.main()
