"""Unit test suite for atomic temp file write and fsync resilience."""

import os
import tempfile
import unittest
from pathlib import Path


def atomic_write_file_safely(target_path: Path, content: str) -> bool:
    if not isinstance(target_path, Path) or not isinstance(content, str):
        return False
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=str(target_path.parent), delete=False) as tf:
            tf.write(content)
            tf.flush()
            os.fsync(tf.fileno())
            temp_name = tf.name
        os.replace(temp_name, str(target_path))
        return True
    except Exception:
        return False


class TestTempFileFlusherResilience(unittest.TestCase):
    def test_atomic_write_file_valid(self):
        target = Path("/tmp/test_atomic_write.txt")
        ok = atomic_write_file_safely(target, "atomic content")
        self.assertTrue(ok)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(), "atomic content")
        if target.exists():
            target.unlink()

    def test_atomic_write_file_invalid(self):
        self.assertFalse(atomic_write_file_safely(None, "content"))


if __name__ == "__main__":
    unittest.main()
