"""Unit test suite for model memory parameter estimation resilience."""

import unittest
from model_memory import estimated_param_billions, _positive_number


class TestModelMemoryResilience(unittest.TestCase):
    def test_positive_number_valid(self):
        self.assertEqual(_positive_number(7.0), 7.0)

    def test_positive_number_invalid(self):
        self.assertEqual(_positive_number("invalid"), 0.0)

    def test_estimated_param_billions_from_metadata(self):
        model = {"total_params_b": 7.0}
        self.assertEqual(estimated_param_billions(model), 7.0)


if __name__ == "__main__":
    unittest.main()
