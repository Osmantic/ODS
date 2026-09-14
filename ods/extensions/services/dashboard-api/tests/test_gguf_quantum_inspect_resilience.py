"""Unit test suite for GGUF quantization scheme inspector resilience."""

import unittest
from gguf_inspector import inspect_gguf


class TestGGUFQuantumInspectResilience(unittest.TestCase):
    def test_inspect_gguf_nonexistent_file(self):
        info = inspect_gguf("/tmp/nonexistent_model.gguf")
        self.assertIsInstance(info, dict)
        self.assertIn("exists", info)

    def test_inspect_gguf_returns_dict(self):
        info = inspect_gguf("")
        self.assertIsInstance(info, dict)


if __name__ == "__main__":
    unittest.main()
