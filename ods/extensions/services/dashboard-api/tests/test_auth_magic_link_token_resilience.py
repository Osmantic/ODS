"""Unit test suite for magic link token format validation resilience."""

import unittest


def validate_magic_link_token_format(token: str | None, min_length: int = 16) -> tuple[bool, str]:
    if not token or not isinstance(token, str):
        return False, "missing_token"
    token_clean = token.strip()
    if len(token_clean) < min_length:
        return False, "token_too_short"
    return True, "valid"


class TestAuthMagicLinkTokenResilience(unittest.TestCase):
    def test_validate_token_valid(self):
        ok, reason = validate_magic_link_token_format("a1b2c3d4e5f6g7h8i9j0")
        self.assertTrue(ok)
        self.assertEqual(reason, "valid")

    def test_validate_token_too_short(self):
        ok, reason = validate_magic_link_token_format("short_tok")
        self.assertFalse(ok)
        self.assertEqual(reason, "token_too_short")

    def test_validate_token_none(self):
        ok, reason = validate_magic_link_token_format(None)
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_token")


if __name__ == "__main__":
    unittest.main()
