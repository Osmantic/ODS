"""Unit test suite for nested dictionary dot path extraction resilience."""

import unittest


def extract_nested_dict_key_path(d: dict | None, path_str: str | None, default: object = None) -> object:
    if not d or not isinstance(d, dict) or not path_str or not isinstance(path_str, str):
        return default
    keys = [k.strip() for k in path_str.split(".") if k.strip()]
    curr = d
    for key in keys:
        if isinstance(curr, dict) and key in curr:
            curr = curr[key]
        else:
            return default
    return curr


class TestDictNestedKeyExtractorResilience(unittest.TestCase):
    def test_extract_nested_valid(self):
        data = {"a": {"b": {"c": 42}}}
        self.assertEqual(extract_nested_dict_key_path(data, "a.b.c"), 42)

    def test_extract_nested_missing(self):
        data = {"a": {"b": 1}}
        self.assertEqual(extract_nested_dict_key_path(data, "a.c", default="N/A"), "N/A")

    def test_extract_nested_none(self):
        self.assertIsNone(extract_nested_dict_key_path(None, "a.b"))


if __name__ == "__main__":
    unittest.main()
