"""Unit test suite for dictionary null value compaction resilience."""

import unittest


def compact_dictionary_none_values(d: dict | None) -> dict:
    if not d or not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if v is not None}


class TestDictCompactNoneResilience(unittest.TestCase):
    def test_compact_dict_valid(self):
        data = {"a": 1, "b": None, "c": "hello", "d": None}
        compacted = compact_dictionary_none_values(data)
        self.assertEqual(compacted, {"a": 1, "c": "hello"})

    def test_compact_dict_none(self):
        self.assertEqual(compact_dictionary_none_values(None), {})


if __name__ == "__main__":
    unittest.main()
