"""Unit test suite for list empty string filtering resilience."""

import unittest


def filter_empty_strings_from_list(items: list | None) -> list[str]:
    if not items or not isinstance(items, list):
        return []
    return [str(x).strip() for x in items if x is not None and str(x).strip()]


class TestListFilterEmptyResilience(unittest.TestCase):
    def test_filter_empty_valid(self):
        res = filter_empty_strings_from_list(["a", "", "  ", "b", None, "c"])
        self.assertEqual(res, ["a", "b", "c"])

    def test_filter_empty_none(self):
        self.assertEqual(filter_empty_strings_from_list(None), [])


if __name__ == "__main__":
    unittest.main()
