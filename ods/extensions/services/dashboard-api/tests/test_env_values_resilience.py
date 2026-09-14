"""Unit test suite for environment values schema parsing resilience."""

import unittest
from env_values import strip_matching_quotes


class TestEnvValuesResilience(unittest.TestCase):
    def test_strip_matching_double_quotes(self):
        self.assertEqual(strip_matching_quotes('"hello"'), "hello")

    def test_strip_matching_single_quotes(self):
        self.assertEqual(strip_matching_quotes("'world'"), "world")

    def test_unmatched_quotes_preserved(self):
        self.assertEqual(strip_matching_quotes('"mixed\''), '"mixed\'')

    def test_plain_string_preserved(self):
        self.assertEqual(strip_matching_quotes("plain_value"), "plain_value")


if __name__ == "__main__":
    unittest.main()
