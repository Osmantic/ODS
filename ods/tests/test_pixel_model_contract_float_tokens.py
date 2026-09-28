"""pixel_model_contract projection must reject float maxTokens overrides."""
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bin"))

from pixel_model_contract import projection, ModelError


class ModelContractFloatTokensTests(unittest.TestCase):
    def test_float_max_tokens_rejected(self):
        config = {
            "agents": {
                "list": [{"id": "pixel", "model": "ods-local/default"}],
                "defaults": {"params": {"maxTokens": 1024.5}}
            },
            "models": {
                "providers": {
                    "ods-local": {"models": [{"id": "default", "name": "ODS Local default", "contextWindow": 8192, "maxTokens": 2048, "reasoning": False}]}
                }
            },
            "plugins": {"entries": {"pixel-ods": {"enabled": True}}}
        }
        with self.assertRaises(ModelError) as ctx:
            projection(config)
        self.assertEqual(str(ctx.exception), "invalid-model-limits")


if __name__ == "__main__":
    unittest.main()
