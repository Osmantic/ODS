"""Unit test suite for list dictionary sorting resilience."""

import unittest


def sort_dictionary_list_by_key(
    items: list[dict] | None, sort_key: str, reverse: bool = False
) -> list[dict]:
    if not items or not isinstance(items, list):
        return []
    if not sort_key or not isinstance(sort_key, str):
        return list(items)

    def _safe_key(d: object) -> str:
        if isinstance(d, dict) and sort_key in d and d[sort_key] is not None:
            return str(d[sort_key]).lower()
        return ""

    try:
        return sorted(items, key=_safe_key, reverse=bool(reverse))
    except Exception:
        return list(items)


class TestListSortKeyResilience(unittest.TestCase):
    def test_sort_dict_list_valid(self):
        data = [{"name": "Charlie"}, {"name": "Alice"}, {"name": "Bob"}]
        res = sort_dictionary_list_by_key(data, "name")
        self.assertEqual([x["name"] for x in res], ["Alice", "Bob", "Charlie"])

    def test_sort_dict_list_none(self):
        self.assertEqual(sort_dictionary_list_by_key(None, "name"), [])


if __name__ == "__main__":
    unittest.main()
