"""Unit test suite for dictionary predicate filtering resilience."""

import unittest


def filter_dictionary_by_predicate(d: dict | None, predicate_fn: callable) -> dict:
    if not d or not isinstance(d, dict):
        return {}
    if not callable(predicate_fn):
        return dict(d)
    res = {}
    for k, v in d.items():
        try:
            if predicate_fn(k, v):
                res[k] = v
        except Exception:
            pass
    return res


class TestDictSelectMatchingResilience(unittest.TestCase):
    def test_filter_dict_valid(self):
        data = {"a": 10, "b": 5, "c": 20}
        filtered = filter_dictionary_by_predicate(data, lambda k, v: v >= 10)
        self.assertEqual(filtered, {"a": 10, "c": 20})

    def test_filter_dict_none(self):
        self.assertEqual(filter_dictionary_by_predicate(None, lambda k, v: True), {})


if __name__ == "__main__":
    unittest.main()
