"""Unit test suite for HTTP Bearer token string parsing resilience."""

import unittest


def extract_bearer_token_string(auth_header: str | None) -> str | None:
    if not auth_header or not isinstance(auth_header, str):
        return None
    parts = auth_header.strip().split()
    if len(parts) != 2:
        return None
    if parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token if token else None


class TestBearerTokenParserResilience(unittest.TestCase):
    def test_extract_bearer_token_valid(self):
        token = extract_bearer_token_string("Bearer secret_token_123")
        self.assertEqual(token, "secret_token_123")

    def test_extract_bearer_token_invalid_prefix(self):
        self.assertIsNone(extract_bearer_token_string("Basic username:password"))

    def test_extract_bearer_token_none(self):
        self.assertIsNone(extract_bearer_token_string(None))


if __name__ == "__main__":
    unittest.main()
