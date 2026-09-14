"""Unit test suite for safe nested dictionary key retrieval resilience."""

import unittest


def get_nested_dictionary_value(d: dict | None, path_keys: list[str] | None, default: object = None) -> object:
    if not d or not isinstance(d, dict) or not path_keys or not isinstance(path_keys, list):
        return default
    curr = d
    for key in path_keys:
        if not isinstance(curr, dict) or key not in curr:
            return default
        curr = curr[key]
    return curr


class TestDictGetNestedKeyResilience(unittest.TestCase):
    def test_get_nested_dict_valid(self):
        data = {"a": {"b": {"c": 100}}}
        self.assertEqual(get_nested_dictionary_value(data, ["a", "b", "c"]), 100)

    def test_get_nested_dict_none(self):
        self.assertEqual(get_nested_dictionary_value(None, ["a"]), None)


if __name__ == "__main__":
    unittest.main()
