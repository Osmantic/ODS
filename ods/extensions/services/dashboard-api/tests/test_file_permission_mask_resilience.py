"""Unit test suite for POSIX file mode permission mask validation resilience."""

import unittest


def validate_posix_file_mode(mode_val: object, default_mode: int = 0o600) -> int:
    if mode_val is None:
        return default_mode
    try:
        if isinstance(mode_val, str):
            mode = int(mode_val.strip(), 8)
        else:
            mode = int(mode_val)
        if 0o000 <= mode <= 0o777:
            return mode
        return default_mode
    except (TypeError, ValueError):
        return default_mode


class TestFilePermissionMaskResilience(unittest.TestCase):
    def test_validate_posix_mode_valid(self):
        self.assertEqual(validate_posix_file_mode(0o644), 0o644)
        self.assertEqual(validate_posix_file_mode("600"), 0o600)

    def test_validate_posix_mode_invalid(self):
        self.assertEqual(validate_posix_file_mode("invalid"), 0o600)
        self.assertEqual(validate_posix_file_mode(None), 0o600)


if __name__ == "__main__":
    unittest.main()
