"""Unit test suite for dictionary values list extraction resilience."""

import unittest


def extract_dictionary_values_as_list(d: dict | None) -> list:
    if not d or not isinstance(d, dict):
        return []
    return list(d.values())


class TestDictValuesAsListResilience(unittest.TestCase):
    def test_extract_dict_values_valid(self):
        data = {"a": 10, "b": 20, "c": 30}
        self.assertEqual(extract_dictionary_values_as_list(data), [10, 20, 30])

    def test_extract_dict_values_none(self):
        self.assertEqual(extract_dictionary_values_as_list(None), [])


if __name__ == "__main__":
    unittest.main()
