"""Tests for GGUF metadata inspection and model memory estimation safety."""

import unittest

from gguf_inspector import inspect_gguf
from model_memory import estimated_param_billions, _positive_number


class TestGGUFMemorySafety(unittest.TestCase):
    def test_inspect_gguf_nonexistent_file(self):
        res = inspect_gguf("/tmp/definitely_nonexistent_model.gguf")
        self.assertFalse(res["exists"])
        self.assertFalse(res["readable"])
        self.assertEqual(res["architecture"], "unknown")
        self.assertEqual(res["quantization"], "unknown")

    def test_positive_number_parsing(self):
        self.assertEqual(_positive_number("7.5"), 7.5)
        self.assertEqual(_positive_number("14"), 14.0)
        self.assertEqual(_positive_number("-5"), 0.0)
        self.assertEqual(_positive_number("invalid"), 0.0)
        self.assertEqual(_positive_number(None), 0.0)
        self.assertEqual(_positive_number(float("inf")), 0.0)
        self.assertEqual(_positive_number(float("nan")), 0.0)

    def test_estimated_param_billions_parsing(self):
        self.assertEqual(estimated_param_billions("llama-3-8b-instruct.gguf"), 8.0)
        self.assertEqual(estimated_param_billions("qwen-2.5-72b-instruct.gguf"), 72.0)
        self.assertEqual(estimated_param_billions("mistral-7b-v0.1"), 7.0)
        self.assertEqual(estimated_param_billions("unknown-model-name.gguf"), 4.0)


if __name__ == "__main__":
    unittest.main()
