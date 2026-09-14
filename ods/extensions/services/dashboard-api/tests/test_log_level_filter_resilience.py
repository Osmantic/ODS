"""Unit test suite for logging level string normalization resilience."""

import unittest

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def normalize_logging_level(level_str: str | None) -> str:
    if not level_str or not isinstance(level_str, str):
        return "INFO"
    cleaned = level_str.strip().upper()
    return cleaned if cleaned in _VALID_LOG_LEVELS else "INFO"


class TestLogLevelFilterResilience(unittest.TestCase):
    def test_normalize_log_level_valid(self):
        self.assertEqual(normalize_logging_level("debug"), "DEBUG")

    def test_normalize_log_level_invalid(self):
        self.assertEqual(normalize_logging_level("verbose"), "INFO")

    def test_normalize_log_level_none(self):
        self.assertEqual(normalize_logging_level(None), "INFO")


if __name__ == "__main__":
    unittest.main()
