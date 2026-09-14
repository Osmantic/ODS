"""Unit test suite for process exit code status evaluation resilience."""

import unittest


def parse_process_exit_status(exit_code: int | None) -> tuple[bool, str]:
    if exit_code is None or not isinstance(exit_code, int):
        return False, "unknown_exit_code"
    if exit_code == 0:
        return True, "success"
    return False, f"failed_code_{exit_code}"


class TestProcessExitCodeResilience(unittest.TestCase):
    def test_parse_exit_code_success(self):
        ok, reason = parse_process_exit_status(0)
        self.assertTrue(ok)
        self.assertEqual(reason, "success")

    def test_parse_exit_code_failure(self):
        ok, reason = parse_process_exit_status(1)
        self.assertFalse(ok)
        self.assertEqual(reason, "failed_code_1")

    def test_parse_exit_code_none(self):
        ok, reason = parse_process_exit_status(None)
        self.assertFalse(ok)
        self.assertEqual(reason, "unknown_exit_code")


if __name__ == "__main__":
    unittest.main()
