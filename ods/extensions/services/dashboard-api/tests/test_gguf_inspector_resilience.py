"""Unit test suite for GGUF metadata inspector resilience."""

import unittest
from pathlib import Path
from gguf_inspector import inspect_gguf


class TestGgufInspectorResilience(unittest.TestCase):
    def test_inspect_nonexistent_gguf_file(self):
        missing_path = Path("/tmp/nonexistent_gguf_model_file.gguf")
        result = inspect_gguf(missing_path)
        self.assertIsInstance(result, dict)
        self.assertFalse(result.get("exists"))
        self.assertFalse(result.get("readable"))

    def test_inspect_invalid_header_gguf_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=True) as tmp:
            tmp.write(b"INVALID_HEADER_DATA")
            tmp.flush()
            result = inspect_gguf(Path(tmp.name))
            self.assertIsInstance(result, dict)
            self.assertFalse(result.get("readable"))


if __name__ == "__main__":
    unittest.main()
