"""Unit test suite for dictionary value transformation mapping resilience."""

import unittest


def map_dictionary_values_safely(d: dict | None, transform_fn: callable) -> dict:
    if not d or not isinstance(d, dict) or not callable(transform_fn):
        return {}
    res = {}
    for k, v in d.items():
        try:
            res[k] = transform_fn(v)
        except Exception:
            res[k] = v
    return res


class TestDictValueTransformResilience(unittest.TestCase):
    def test_map_dict_values_valid(self):
        mapped = map_dictionary_values_safely({"a": 1, "b": 2}, lambda x: x * 10)
        self.assertEqual(mapped, {"a": 10, "b": 20})

    def test_map_dict_values_exception(self):
        mapped = map_dictionary_values_safely({"a": 1, "b": "str"}, lambda x: x + 5)
        self.assertEqual(mapped, {"a": 6, "b": "str"})

    def test_map_dict_values_none(self):
        self.assertEqual(map_dictionary_values_safely(None, str), {})


if __name__ == "__main__":
    unittest.main()
