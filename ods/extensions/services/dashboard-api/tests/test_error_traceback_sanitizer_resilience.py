"""Unit test suite for error traceback message sanitization resilience."""

import unittest


def sanitize_error_traceback(error_msg: str | Exception | None) -> str:
    if error_msg is None:
        return "Unknown error occurred"
    if isinstance(error_msg, Exception):
        msg = str(error_msg)
    else:
        msg = str(error_msg)
    cleaned = msg.strip()
    return cleaned if cleaned else "Unknown error occurred"


class TestErrorTracebackSanitizerResilience(unittest.TestCase):
    def test_sanitize_error_string(self):
        self.assertEqual(sanitize_error_traceback("  Connection reset  "), "Connection reset")

    def test_sanitize_error_exception(self):
        self.assertEqual(sanitize_error_traceback(ValueError("invalid port")), "invalid port")

    def test_sanitize_error_none(self):
        self.assertEqual(sanitize_error_traceback(None), "Unknown error occurred")


if __name__ == "__main__":
    unittest.main()
