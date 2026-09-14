"""Unit test suite for dictionary key renaming mapping resilience."""

import unittest


def rename_dictionary_keys_safely(d: dict | None, key_mapping: dict[str, str] | None) -> dict:
    if not d or not isinstance(d, dict):
        return {}
    if not key_mapping or not isinstance(key_mapping, dict):
        return dict(d)
    res = {}
    for k, v in d.items():
        new_k = key_mapping.get(k, k)
        if new_k is not None:
            res[str(new_k)] = v
    return res


class TestDictRenameKeysResilience(unittest.TestCase):
    def test_rename_dict_keys_valid(self):
        data = {"old_name": "server1", "status": "active"}
        renamed = rename_dictionary_keys_safely(data, {"old_name": "hostname"})
        self.assertEqual(renamed, {"hostname": "server1", "status": "active"})

    def test_rename_dict_keys_none(self):
        self.assertEqual(rename_dictionary_keys_safely(None, {"a": "b"}), {})


if __name__ == "__main__":
    unittest.main()
