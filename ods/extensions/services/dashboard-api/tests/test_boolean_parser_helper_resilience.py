"""Unit test suite for loose boolean string parsing resilience."""

import unittest

_TRUE_SET = {"true", "1", "yes", "on", "enable", "enabled"}


def parse_loose_boolean_value(val: object, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()
    return s in _TRUE_SET


class TestBooleanParserHelperResilience(unittest.TestCase):
    def test_parse_boolean_valid(self):
        self.assertTrue(parse_loose_boolean_value("true"))
        self.assertTrue(parse_loose_boolean_value("YES"))
        self.assertTrue(parse_loose_boolean_value(1))

    def test_parse_boolean_false(self):
        self.assertFalse(parse_loose_boolean_value("false"))
        self.assertFalse(parse_loose_boolean_value(0))

    def test_parse_boolean_none(self):
        self.assertFalse(parse_loose_boolean_value(None, default=False))


if __name__ == "__main__":
    unittest.main()
