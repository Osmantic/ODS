"""Verify target model contract rejects names with leading or trailing whitespace."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_model_contract import target, ModelError

class ModelTargetWhitespaceTests(unittest.TestCase):
    def test_whitespace_in_model_name_rejected(self):
        for bad in (" qwen2.5", "qwen2.5 ", " qwen2.5 "):
            val = {
                "model": bad,
                "contextLength": 8192,
                "maxTokens": 2048,
                "reasoning": False
            }
            with self.assertRaises(ModelError):
                target(val)

    def test_clean_model_name_accepted(self):
        val = {
            "model": "qwen2.5:7b",
            "contextLength": 8192,
            "maxTokens": 2048,
            "reasoning": False
        }
        res = target(val)
        self.assertEqual(res["model"], "qwen2.5:7b")

if __name__ == "__main__":
    unittest.main()
