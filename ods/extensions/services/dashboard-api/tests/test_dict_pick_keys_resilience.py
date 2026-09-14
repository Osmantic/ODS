"""Unit test suite for dictionary specific key extraction resilience."""

import unittest


def pick_dictionary_keys_safely(d: dict | None, keys_to_keep: list[str] | set[str] | None) -> dict:
    if not d or not isinstance(d, dict) or not keys_to_keep:
        return {}
    target_keys = set(keys_to_keep)
    return {k: v for k, v in d.items() if k in target_keys}


class TestDictPickKeysResilience(unittest.TestCase):
    def test_pick_dict_keys_valid(self):
        data = {"name": "Alice", "age": 30, "role": "admin"}
        picked = pick_dictionary_keys_safely(data, ["name", "role"])
        self.assertEqual(picked, {"name": "Alice", "role": "admin"})

    def test_pick_dict_keys_none(self):
        self.assertEqual(pick_dictionary_keys_safely(None, ["a"]), {})


if __name__ == "__main__":
    unittest.main()
