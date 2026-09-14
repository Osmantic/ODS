"""Unit test suite for token spy middleware auth headers resilience."""

import unittest


def build_token_spy_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key and isinstance(api_key, str) and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    return headers


class TestTokenSpyResilience(unittest.TestCase):
    def test_build_token_spy_headers_with_key(self):
        headers = build_token_spy_headers("secret_key_123")
        self.assertEqual(headers["Authorization"], "Bearer secret_key_123")

    def test_build_token_spy_headers_empty_key(self):
        headers = build_token_spy_headers("")
        self.assertNotIn("Authorization", headers)


if __name__ == "__main__":
    unittest.main()
