"""Unit test suite for alphabetical dictionary key sorting resilience."""

import unittest


def sort_dictionary_keys_alphabetically(d: dict | None) -> dict:
    if not d or not isinstance(d, dict):
        return {}
    return {str(k): d[k] for k in sorted(d.keys(), key=lambda x: str(x).lower())}


class TestDictSortKeysAlphabeticalResilience(unittest.TestCase):
    def test_sort_dict_keys_valid(self):
        data = {"b": 2, "a": 1, "c": 3}
        sorted_dict = sort_dictionary_keys_alphabetically(data)
        self.assertEqual(list(sorted_dict.keys()), ["a", "b", "c"])

    def test_sort_dict_keys_none(self):
        self.assertEqual(sort_dictionary_keys_alphabetically(None), {})


if __name__ == "__main__":
    unittest.main()
