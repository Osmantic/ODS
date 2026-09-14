"""Unit test suite for safe nested dictionary key flattening resilience."""

import unittest


def flatten_dictionary_keys_safely(d: dict | None, parent_key: str = "", sep: str = ".") -> dict:
    if not d or not isinstance(d, dict):
        return {}
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else str(k)
        if isinstance(v, dict) and v:
            items.extend(flatten_dictionary_keys_safely(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


class TestDictFlattenKeysResilience(unittest.TestCase):
    def test_flatten_keys_valid(self):
        data = {"a": {"b": 1, "c": {"d": 2}}, "e": 3}
        self.assertEqual(flatten_dictionary_keys_safely(data), {"a.b": 1, "a.c.d": 2, "e": 3})

    def test_flatten_keys_none(self):
        self.assertEqual(flatten_dictionary_keys_safely(None), {})


if __name__ == "__main__":
    unittest.main()
