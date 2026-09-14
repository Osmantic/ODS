"""Unit test suite for escaped quote string unescaping resilience."""

import unittest


def unescape_quote_characters_safely(raw_str: str | None) -> str:
    if not raw_str or not isinstance(raw_str, str):
        return ""
    return raw_str.replace('\\"', '"').replace("\\'", "'")


class TestStringUnescapeQuotesResilience(unittest.TestCase):
    def test_unescape_quotes_valid(self):
        self.assertEqual(unescape_quote_characters_safely('hello \\"world\\"'), 'hello "world"')

    def test_unescape_quotes_none(self):
        self.assertEqual(unescape_quote_characters_safely(None), "")


if __name__ == "__main__":
    unittest.main()
