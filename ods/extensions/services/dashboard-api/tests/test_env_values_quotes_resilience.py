"""Unit test suite for env values reader quotes resilience."""

import unittest
from env_values import strip_matching_quotes


class TestEnvValuesQuotesResilience(unittest.TestCase):
    def test_strip_matching_double_quotes(self):
        self.assertEqual(strip_matching_quotes('"hello"'), "hello")

    def test_strip_matching_single_quotes(self):
        self.assertEqual(strip_matching_quotes("'world'"), "world")

    def test_unmatched_quotes_preserved(self):
        self.assertEqual(strip_matching_quotes('"unmatched'), '"unmatched')


if __name__ == "__main__":
    unittest.main()
