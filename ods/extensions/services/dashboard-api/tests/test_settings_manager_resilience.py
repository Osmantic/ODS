"""Unit test suite for settings helpers parsing resilience."""

import unittest
from settings import _ENV_ASSIGNMENT_RE, _SENSITIVE_ENV_KEY_RE


class TestSettingsManagerResilience(unittest.TestCase):
    def test_env_assignment_regex_match(self):
        match = _ENV_ASSIGNMENT_RE.match("FOO=bar")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "FOO")
        self.assertEqual(match.group(2), "bar")

    def test_sensitive_env_key_regex_match(self):
        self.assertIsNotNone(_SENSITIVE_ENV_KEY_RE.search("ODS_SESSION_SECRET"))
        self.assertIsNotNone(_SENSITIVE_ENV_KEY_RE.search("TOKEN_SPY_API_KEY"))


if __name__ == "__main__":
    unittest.main()
