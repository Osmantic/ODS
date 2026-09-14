"""Unit test suite for CORS origin URL normalization resilience."""

import unittest


def normalize_cors_origin_url(origin: str | None) -> str | None:
    if not origin or not isinstance(origin, str):
        return None
    cleaned = origin.strip().rstrip("/")
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        return None
    return cleaned


class TestCORSOriginNormalizerResilience(unittest.TestCase):
    def test_normalize_origin_valid(self):
        self.assertEqual(normalize_cors_origin_url("http://localhost:3000/"), "http://localhost:3000")

    def test_normalize_origin_invalid(self):
        self.assertIsNone(normalize_cors_origin_url("invalid_origin"))
        self.assertIsNone(normalize_cors_origin_url(None))


if __name__ == "__main__":
    unittest.main()
