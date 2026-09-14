"""Unit test suite for RAM/GPU memory byte formatting resilience."""

import unittest


def format_memory_bytes(bytes_val: int | float | None) -> str:
    if bytes_val is None or not isinstance(bytes_val, (int, float)) or bytes_val < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    val = float(bytes_val)
    idx = 0
    while val >= 1024.0 and idx < len(units) - 1:
        val /= 1024.0
        idx += 1
    return f"{round(val, 2)} {units[idx]}"


class TestMemoryBytesFormatterResilience(unittest.TestCase):
    def test_format_memory_bytes_mb(self):
        self.assertEqual(format_memory_bytes(1048576), "1.0 MB")

    def test_format_memory_bytes_gb(self):
        self.assertEqual(format_memory_bytes(1073741824), "1.0 GB")

    def test_format_memory_bytes_none(self):
        self.assertEqual(format_memory_bytes(None), "0 B")


if __name__ == "__main__":
    unittest.main()
