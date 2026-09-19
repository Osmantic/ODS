"""Verify target rejects model names with leading/trailing whitespace."""
import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "bin"))

from pixel_model_contract import target, ModelError

class PixelModelUnstrippedNameTests(unittest.TestCase):
    def setUp(self):
        self.valid_target = {
            "model": "llama-3-8b",
            "contextLength": 8192,
            "maxTokens": 2048,
            "reasoning": False,
        }

    def test_valid_model_name(self):
        res = target(self.valid_target)
        self.assertEqual(res["model"], "llama-3-8b")

    def test_unstripped_whitespace_rejected(self):
        for bad in (" llama-3-8b", "llama-3-8b ", " llama-3-8b "):
            with self.assertRaises(ModelError):
                target(dict(self.valid_target, model=bad))

if __name__ == "__main__":
    unittest.main()
