"""Regression test for Pixel model contract float limit resolution."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from pixel_model_contract import ModelError, projection  # noqa: E402


def _make_config(max_tokens_value):
    return {
        "agents": {
            "defaults": {"compaction": {"mode": "safeguard"}},
            "list": [
                {
                    "id": "pixel",
                    "model": "ods-gateway/ods/current",
                    "contextTokens": 65536,
                    "params": {"max_tokens": max_tokens_value},
                }
            ],
        },
        "models": {
            "providers": {
                "ods-gateway": {
                    "models": [
                        {
                            "id": "ods/current",
                            "name": "ODS Current (Qwen-4B)",
                            "contextWindow": 65536,
                            "maxTokens": 8192,
                            "reasoning": False,
                        }
                    ]
                }
            }
        },
        "plugins": {
            "entries": {
                "pixel-ods": {
                    "enabled": True,
                    "config": {"modelContextWindow": 65536},
                }
            }
        },
    }


class PixelModelContractLimitsTests(unittest.TestCase):
    def test_integer_float_limit_resolves_to_int(self):
        config = _make_config(4096.0)
        result = projection(config)
        self.assertIsInstance(result["limits"]["maxOutputTokens"], int)
        self.assertEqual(result["limits"]["maxOutputTokens"], 4096)

    def test_regular_int_limit_resolves_to_int(self):
        config = _make_config(2048)
        result = projection(config)
        self.assertIsInstance(result["limits"]["maxOutputTokens"], int)
        self.assertEqual(result["limits"]["maxOutputTokens"], 2048)

    def test_fractional_float_limit_rejected(self):
        config = _make_config(4096.5)
        with self.assertRaises(ModelError) as ctx:
            projection(config)
        self.assertEqual(str(ctx.exception), "invalid-model-limits")

    def test_non_finite_float_limit_rejected(self):
        config = _make_config(float("inf"))
        with self.assertRaises(ModelError) as ctx:
            projection(config)
        self.assertEqual(str(ctx.exception), "invalid-model-limits")


if __name__ == "__main__":
    unittest.main()
