"""Unit test suite for model download progress tracker resilience."""

import unittest


def calculate_download_progress(downloaded_bytes: int | float, total_bytes: int | float) -> float:
    if not isinstance(downloaded_bytes, (int, float)) or not isinstance(total_bytes, (int, float)):
        return 0.0
    if total_bytes <= 0 or downloaded_bytes < 0:
        return 0.0
    progress = (downloaded_bytes / total_bytes) * 100.0
    return round(min(progress, 100.0), 2)


class TestModelDownloadTrackerResilience(unittest.TestCase):
    def test_calculate_download_progress_valid(self):
        self.assertEqual(calculate_download_progress(50, 100), 50.0)

    def test_calculate_download_progress_zero_total(self):
        self.assertEqual(calculate_download_progress(50, 0), 0.0)

    def test_calculate_download_progress_overflow(self):
        self.assertEqual(calculate_download_progress(150, 100), 100.0)


if __name__ == "__main__":
    unittest.main()
