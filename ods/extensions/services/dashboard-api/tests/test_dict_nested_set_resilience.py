"""Unit test suite for safe nested dictionary key path value setting resilience."""

import unittest


def set_nested_dictionary_value_path(d: dict | None, path_keys: list[str] | None, value: object) -> dict:
    if d is None or not isinstance(d, dict):
        base = {}
    else:
        base = dict(d)
    if not path_keys or not isinstance(path_keys, list):
        return base
    curr = base
    for k in path_keys[:-1]:
        key_str = str(k)
        if key_str not in curr or not isinstance(curr[key_str], dict):
            curr[key_str] = {}
        curr = curr[key_str]
    curr[str(path_keys[-1])] = value
    return base


class TestDictNestedSetResilience(unittest.TestCase):
    def test_set_nested_dict_valid(self):
        res = set_nested_dictionary_value_path({}, ["a", "b", "c"], 42)
        self.assertEqual(res, {"a": {"b": {"c": 42}}})

    def test_set_nested_dict_none(self):
        self.assertEqual(set_nested_dictionary_value_path(None, ["a"], 10), {"a": 10})


if __name__ == "__main__":
    unittest.main()
