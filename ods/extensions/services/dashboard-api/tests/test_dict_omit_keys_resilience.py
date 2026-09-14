"""Unit test suite for dictionary blacklist key omission resilience."""

import unittest


def omit_dictionary_keys_safely(d: dict | None, keys_to_omit: list[str] | set[str] | None) -> dict:
    if not d or not isinstance(d, dict):
        return {}
    if not keys_to_omit:
        return dict(d)
    omit_set = set(keys_to_omit)
    return {k: v for k, v in d.items() if k not in omit_set}


class TestDictOmitKeysResilience(unittest.TestCase):
    def test_omit_dict_keys_valid(self):
        data = {"secret_token": "abc", "username": "alice", "pass": "123"}
        cleaned = omit_dictionary_keys_safely(data, ["secret_token", "pass"])
        self.assertEqual(cleaned, {"username": "alice"})

    def test_omit_dict_keys_none(self):
        self.assertEqual(omit_dictionary_keys_safely(None, ["a"]), {})


if __name__ == "__main__":
    unittest.main()
