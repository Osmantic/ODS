"""Unit test suite for sensitive string masking resilience."""

import unittest


def mask_sensitive_token_string(token: str | None, unmasked_suffix_len: int = 4) -> str:
    if not token or not isinstance(token, str):
        return ""
    cleaned = token.strip()
    suffix_len = max(0, min(16, int(unmasked_suffix_len)))
    if len(cleaned) <= suffix_len:
        return "*" * len(cleaned)
    masked_part = "*" * (len(cleaned) - suffix_len)
    return masked_part + cleaned[-suffix_len:]


class TestStringMaskSensitiveResilience(unittest.TestCase):
    def test_mask_sensitive_valid(self):
        self.assertEqual(mask_sensitive_token_string("secret_api_key_1234"), "***************1234")

    def test_mask_sensitive_short(self):
        self.assertEqual(mask_sensitive_token_string("abc"), "***")

    def test_mask_sensitive_none(self):
        self.assertEqual(mask_sensitive_token_string(None), "")


if __name__ == "__main__":
    unittest.main()
