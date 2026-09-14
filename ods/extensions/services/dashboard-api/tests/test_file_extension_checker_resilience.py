"""Unit test suite for file extension validation resilience."""

import unittest
from pathlib import Path


def is_allowed_file_extension(filename: str | Path | None, allowed_exts: set[str]) -> bool:
    if not filename or not allowed_exts:
        return False
    path = Path(filename) if isinstance(filename, str) else filename
    if not isinstance(path, Path) or not path.suffix:
        return False
    ext = path.suffix.lower().lstrip(".")
    return ext in {e.lower().lstrip(".") for e in allowed_exts}


class TestFileExtensionCheckerResilience(unittest.TestCase):
    def test_is_allowed_extension_valid(self):
        allowed = {"gguf", "bin", "json"}
        self.assertTrue(is_allowed_file_extension("model.Q4_K_M.gguf", allowed))
        self.assertTrue(is_allowed_file_extension(Path("config.json"), allowed))

    def test_is_allowed_extension_invalid(self):
        allowed = {"gguf", "json"}
        self.assertFalse(is_allowed_file_extension("script.py", allowed))
        self.assertFalse(is_allowed_file_extension(None, allowed))


if __name__ == "__main__":
    unittest.main()
