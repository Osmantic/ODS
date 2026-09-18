"""Verify valid_model_contract rejects model names with unstripped whitespace."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "extensions/services/pixel-edge"))

from access_mode import valid_model_contract

class ModelContractWhitespaceTests(unittest.TestCase):
    def test_unstripped_model_rejected(self):
        for bad in (" qwen2.5", "qwen2.5 ", " qwen2.5 "):
            val = {"model": bad, "contextLength": 8192, "maxTokens": 2048, "reasoning": False}
            self.assertFalse(valid_model_contract(val))

    def test_clean_model_accepted(self):
        val = {"model": "qwen2.5:7b", "contextLength": 8192, "maxTokens": 2048, "reasoning": False}
        self.assertTrue(valid_model_contract(val))

if __name__ == "__main__":
    unittest.main()
