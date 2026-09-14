"""Unit tests for env_values.py parsing, boolean conversion, and key sanitization."""

import unittest

from env_values import parse_bool_env, sanitize_env_key, strip_matching_quotes


class TestEnvValuesResilience(unittest.TestCase):
    def test_strip_matching_quotes_edge_cases(self):
        self.assertEqual(strip_matching_quotes('"hello"'), "hello")
        self.assertEqual(strip_matching_quotes("'world'"), "world")
        self.assertEqual(strip_matching_quotes('"unmatched'), '"unmatched')
        self.assertEqual(strip_matching_quotes(None), "")
        self.assertEqual(strip_matching_quotes(123), "")

    def test_parse_bool_env(self):
        self.assertTrue(parse_bool_env("true"))
        self.assertTrue(parse_bool_env("YES"))
        self.assertTrue(parse_bool_env("1"))
        self.assertFalse(parse_bool_env("false"))
        self.assertFalse(parse_bool_env("0"))
        self.assertTrue(parse_bool_env(None, default=True))
        self.assertFalse(parse_bool_env("unknown", default=False))

    def test_sanitize_env_key(self):
        self.assertEqual(sanitize_env_key("VALID_KEY"), "VALID_KEY")
        self.assertEqual(sanitize_env_key("key-with-dashes"), "key_with_dashes")
        self.assertEqual(sanitize_env_key("123invalid"), "")
        self.assertEqual(sanitize_env_key(None), "")


if __name__ == "__main__":
    unittest.main()
