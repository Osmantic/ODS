"""Unit test suite for dictionary key renaming resilience."""

import unittest


def rename_dict_keys_safely(data_dict: dict | None, key_map: dict[str, str] | None) -> dict:
    if not data_dict or not isinstance(data_dict, dict):
        return {}
    if not key_map or not isinstance(key_map, dict):
        return dict(data_dict)
    renamed = {}
    for k, v in data_dict.items():
        new_k = key_map.get(k, k)
        renamed[new_k] = v
    return renamed


class TestDictionaryKeyRenamerResilience(unittest.TestCase):
    def test_rename_dict_keys_valid(self):
        data = {"old_name": "val1", "keep": "val2"}
        mapping = {"old_name": "new_name"}
        res = rename_dict_keys_safely(data, mapping)
        self.assertEqual(res["new_name"], "val1")
        self.assertEqual(res["keep"], "val2")

    def test_rename_dict_keys_none(self):
        self.assertEqual(rename_dict_keys_safely(None, {}), {})


if __name__ == "__main__":
    unittest.main()
