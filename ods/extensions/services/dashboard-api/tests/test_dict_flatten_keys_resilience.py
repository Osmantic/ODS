"""Unit test suite for nested dictionary flattening resilience."""

import unittest


def flatten_dictionary_keys_safely(d: dict | None, parent_key: str = "", sep: str = ".") -> dict:
    if not isinstance(d, dict):
        return {}
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else str(k)
        if isinstance(v, dict):
            items.extend(flatten_dictionary_keys_safely(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


class TestDictFlattenKeysResilience(unittest.TestCase):
    def test_flatten_dict_valid(self):
        data = {"server": {"host": "localhost", "port": 8000}}
        flat = flatten_dictionary_keys_safely(data)
        self.assertEqual(flat["server.host"], "localhost")
        self.assertEqual(flat["server.port"], 8000)

    def test_flatten_dict_none(self):
        self.assertEqual(flatten_dictionary_keys_safely(None), {})


if __name__ == "__main__":
    unittest.main()
