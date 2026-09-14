"""Unit test suite for dictionary falsy value removal resilience."""

import unittest


def compact_dictionary_falsy_values(d: dict | None) -> dict:
    if not d or not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if bool(v)}


class TestDictCompactFalsyResilience(unittest.TestCase):
    def test_compact_falsy_valid(self):
        data = {"a": 1, "b": 0, "c": "hello", "d": "", "e": None, "f": []}
        self.assertEqual(compact_dictionary_falsy_values(data), {"a": 1, "c": "hello"})

    def test_compact_falsy_none(self):
        self.assertEqual(compact_dictionary_falsy_values(None), {})


if __name__ == "__main__":
    unittest.main()
