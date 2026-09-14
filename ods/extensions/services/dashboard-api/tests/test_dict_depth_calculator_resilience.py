"""Unit test suite for recursive dictionary nesting depth calculation resilience."""

import unittest


def calculate_dictionary_nesting_depth(d: object, max_depth: int = 20) -> int:
    if not isinstance(d, dict) or not d:
        return 0
    cap = max(1, min(100, int(max_depth)))

    def _depth(node: object, current: int) -> int:
        if current >= cap or not isinstance(node, dict) or not node:
            return current
        max_sub = current
        for v in node.values():
            if isinstance(v, dict):
                sub_d = _depth(v, current + 1)
                if sub_d > max_sub:
                    max_sub = sub_d
        return max_sub

    return _depth(d, 1)


class TestDictDepthCalculatorResilience(unittest.TestCase):
    def test_dict_depth_valid(self):
        data = {"a": {"b": {"c": 1}}}
        self.assertEqual(calculate_dictionary_nesting_depth(data), 3)

    def test_dict_depth_empty(self):
        self.assertEqual(calculate_dictionary_nesting_depth({}), 0)

    def test_dict_depth_none(self):
        self.assertEqual(calculate_dictionary_nesting_depth(None), 0)


if __name__ == "__main__":
    unittest.main()
