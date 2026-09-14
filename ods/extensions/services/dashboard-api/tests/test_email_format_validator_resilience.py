"""Unit test suite for user email address format validation resilience."""

import re
import unittest

_EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def validate_user_email_address(email_str: str | None) -> tuple[bool, str]:
    if not email_str or not isinstance(email_str, str):
        return False, "missing_email"
    cleaned = email_str.strip().lower()
    if not _EMAIL_REGEX.match(cleaned):
        return False, "invalid_email_format"
    return True, "valid"


class TestEmailFormatValidatorResilience(unittest.TestCase):
    def test_validate_email_valid(self):
        ok, reason = validate_user_email_address("user@example.com")
        self.assertTrue(ok)
        self.assertEqual(reason, "valid")

    def test_validate_email_invalid(self):
        ok, reason = validate_user_email_address("not_an_email")
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_email_format")

    def test_validate_email_none(self):
        ok, reason = validate_user_email_address(None)
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_email")


if __name__ == "__main__":
    unittest.main()
