"""Unit test suite for recursive dictionary deep merging resilience."""

import unittest


def deep_merge_dictionaries_safely(dict1: dict | None, dict2: dict | None) -> dict:
    if not isinstance(dict1, dict):
        base = {}
    else:
        base = dict(dict1)
    if not isinstance(dict2, dict):
        return base

    for k, v in dict2.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            base[k] = deep_merge_dictionaries_safely(base[k], v)
        else:
            base[k] = v
    return base


class TestDictDeepMergeResilience(unittest.TestCase):
    def test_deep_merge_valid(self):
        d1 = {"a": {"b": 1}, "c": 2}
        d2 = {"a": {"d": 3}, "e": 4}
        merged = deep_merge_dictionaries_safely(d1, d2)
        self.assertEqual(merged, {"a": {"b": 1, "d": 3}, "c": 2, "e": 4})

    def test_deep_merge_none(self):
        self.assertEqual(deep_merge_dictionaries_safely(None, {"a": 1}), {"a": 1})
        self.assertEqual(deep_merge_dictionaries_safely({"a": 1}, None), {"a": 1})


if __name__ == "__main__":
    unittest.main()
