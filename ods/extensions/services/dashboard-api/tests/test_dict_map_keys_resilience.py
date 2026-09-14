"""Unit test suite for dictionary key transformation mapping resilience."""

import unittest


def map_dictionary_keys_by_function(d: dict | None, key_fn: callable) -> dict:
    if not d or not isinstance(d, dict):
        return {}
    if not callable(key_fn):
        return dict(d)
    res = {}
    for k, v in d.items():
        try:
            new_k = key_fn(k)
            res[str(new_k)] = v
        except Exception:
            res[str(k)] = v
    return res


class TestDictMapKeysResilience(unittest.TestCase):
    def test_map_dict_keys_valid(self):
        data = {"a": 1, "b": 2}
        mapped = map_dictionary_keys_by_function(data, lambda x: f"prefix_{x}")
        self.assertEqual(mapped, {"prefix_a": 1, "prefix_b": 2})

    def test_map_dict_keys_none(self):
        self.assertEqual(map_dictionary_keys_by_function(None, str.upper), {})


if __name__ == "__main__":
    unittest.main()
