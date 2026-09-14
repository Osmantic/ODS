"""Unit test suite for environment variable key normalization resilience."""

import re
import unittest

_ENV_KEY_VALID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sanitize_env_variable_key(raw_key: str | None) -> str | None:
    if not raw_key or not isinstance(raw_key, str):
        return None
    cleaned = raw_key.strip().upper()
    if not _ENV_KEY_VALID_RE.match(cleaned):
        return None
    return cleaned


class TestEnvKeyNamerResilience(unittest.TestCase):
    def test_sanitize_env_key_valid(self):
        self.assertEqual(sanitize_env_variable_key("service_port"), "SERVICE_PORT")

    def test_sanitize_env_key_invalid(self):
        self.assertIsNone(sanitize_env_variable_key("123INVALID-KEY"))
        self.assertIsNone(sanitize_env_variable_key(None))


if __name__ == "__main__":
    unittest.main()
