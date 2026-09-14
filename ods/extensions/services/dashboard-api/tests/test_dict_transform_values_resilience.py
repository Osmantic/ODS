"""Unit test suite for dictionary value mapper transformation resilience."""

import unittest


def transform_dictionary_values_safely(d: dict | None, value_fn: callable) -> dict:
    if not d or not isinstance(d, dict):
        return {}
    if not callable(value_fn):
        return dict(d)
    res = {}
    for k, v in d.items():
        try:
            res[k] = value_fn(v)
        except Exception:
            res[k] = v
    return res


class TestDictTransformValuesResilience(unittest.TestCase):
    def test_transform_dict_values_valid(self):
        data = {"a": 1, "b": 2}
        transformed = transform_dictionary_values_safely(data, lambda x: x * 10)
        self.assertEqual(transformed, {"a": 10, "b": 20})

    def test_transform_dict_values_none(self):
        self.assertEqual(transform_dictionary_values_safely(None, lambda x: x), {})


if __name__ == "__main__":
    unittest.main()
