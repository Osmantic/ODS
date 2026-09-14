"""Unit test suite for session cookie structure validation resilience."""

import unittest


def validate_session_cookie_structure(cookie_str: str | None) -> tuple[bool, str]:
    if not cookie_str or not isinstance(cookie_str, str):
        return False, "missing_cookie"
    parts = cookie_str.strip().split(".")
    if len(parts) != 2:
        return False, "invalid_format"
    if not parts[0] or not parts[1]:
        return False, "empty_segments"
    return True, "valid"


class TestSessionCookieValidatorResilience(unittest.TestCase):
    def test_validate_cookie_valid(self):
        ok, reason = validate_session_cookie_structure("payload.signature")
        self.assertTrue(ok)
        self.assertEqual(reason, "valid")

    def test_validate_cookie_invalid_parts(self):
        ok, reason = validate_session_cookie_structure("malformed_cookie")
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_format")

    def test_validate_cookie_none(self):
        ok, reason = validate_session_cookie_structure(None)
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_cookie")


if __name__ == "__main__":
    unittest.main()
