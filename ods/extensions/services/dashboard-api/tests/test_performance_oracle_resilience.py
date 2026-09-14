"""Unit test suite for Performance Oracle model advisor resilience."""

import unittest


from performance_oracle import evaluate_performance


class TestPerformanceOracleResilience(unittest.TestCase):
    def test_evaluate_performance_returns_dict(self):
        model = {"id": "test-model", "decode_read_mb": 1000}
        result = evaluate_performance(
            model,
            [],
            {"quantization": "Q4_K_M", "readable": False},
            False,
            0,
            32768,
            {},
            [],
            True,
        )
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
