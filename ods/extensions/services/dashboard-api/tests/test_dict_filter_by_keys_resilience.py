"""Unit test suite for dictionary whitelist key filtering resilience."""

import unittest


def filter_dictionary_by_allowed_keys(d: dict | None, allowed_keys: list[str] | set[str] | None) -> dict:
    if not d or not isinstance(d, dict):
        return {}
    if not allowed_keys:
        return {}
    allowed_set = set(allowed_keys)
    return {k: v for k, v in d.items() if k in allowed_set}


class TestDictFilterByKeysResilience(unittest.TestCase):
    def test_filter_dict_valid(self):
        data = {"host": "localhost", "port": 8080, "debug": True}
        filtered = filter_dictionary_by_allowed_keys(data, ["host", "port"])
        self.assertEqual(filtered, {"host": "localhost", "port": 8080})

    def test_filter_dict_none(self):
        self.assertEqual(filter_dictionary_by_allowed_keys(None, ["host"]), {})


if __name__ == "__main__":
    unittest.main()
