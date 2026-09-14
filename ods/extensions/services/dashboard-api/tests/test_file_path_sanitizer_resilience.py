"""Unit test suite for file path sanitization and directory traversal resilience."""

import unittest
from pathlib import Path


def sanitize_relative_filepath(path_str: str | None) -> Path | None:
    if not path_str or not isinstance(path_str, str):
        return None
    cleaned = path_str.strip()
    if ".." in cleaned or cleaned.startswith("/") or "\\" in cleaned:
        return None
    p = Path(cleaned)
    return p if p.name else None


class TestFilePathSanitizerResilience(unittest.TestCase):
    def test_sanitize_filepath_valid(self):
        p = sanitize_relative_filepath("config/manifest.yaml")
        self.assertIsNotNone(p)
        self.assertEqual(str(p), "config/manifest.yaml")

    def test_sanitize_filepath_traversal(self):
        self.assertIsNone(sanitize_relative_filepath("../etc/passwd"))
        self.assertIsNone(sanitize_relative_filepath("/absolute/path"))

    def test_sanitize_filepath_none(self):
        self.assertIsNone(sanitize_relative_filepath(None))


if __name__ == "__main__":
    unittest.main()
