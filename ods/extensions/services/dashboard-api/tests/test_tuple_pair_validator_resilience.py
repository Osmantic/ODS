"""Unit test suite for key-value pair tuple validation resilience."""

import unittest


def validate_key_value_pair_tuple(pair: object) -> tuple[str, str] | None:
    if not isinstance(pair, (tuple, list)) or len(pair) < 2:
        return None
    k, v = pair[0], pair[1]
    if k is None or not str(k).strip():
        return None
    return str(k).strip(), str(v) if v is not None else ""


class TestTuplePairValidatorResilience(unittest.TestCase):
    def test_validate_pair_valid(self):
        self.assertEqual(validate_key_value_pair_tuple(("key", "value")), ("key", "value"))

    def test_validate_pair_invalid(self):
        self.assertIsNone(validate_key_value_pair_tuple(("   ", "value")))
        self.assertIsNone(validate_key_value_pair_tuple(["only_one"]))

    def test_validate_pair_none(self):
        self.assertIsNone(validate_key_value_pair_tuple(None))


if __name__ == "__main__":
    unittest.main()
