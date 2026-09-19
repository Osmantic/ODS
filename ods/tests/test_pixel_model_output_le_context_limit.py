"""Verify projection rejects maxOutputTokens exceeding contextTokens in limits."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_model_contract import ModelError, projection

class ModelOutputLeContextLimitTests(unittest.TestCase):
    def test_output_exceeding_context_rejected(self):
        config = {
            "agents": {
                "list": [{"id": "pixel", "model": "ods-local/qwen2.5", "params": {"maxTokens": 16000}, "contextTokens": 8192}],
                "defaults": {}
            },
            "models": {
                "providers": {
                    "ods-local": {
                        "models": [{"id": "qwen2.5", "name": "ODS Local qwen2.5", "contextWindow": 8192, "maxTokens": 8192, "reasoning": False}]
                    }
                }
            },
            "plugins": {
                "entries": {
                    "pixel-ods": {"enabled": True, "config": {}}
                }
            }
        }
        with self.assertRaises(ModelError) as ctx:
            projection(config)
        self.assertEqual(str(ctx.exception), "invalid-model-limits")

if __name__ == "__main__":
    unittest.main()
