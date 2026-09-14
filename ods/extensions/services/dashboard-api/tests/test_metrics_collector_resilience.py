"""Unit test suite for telemetry metrics collector resilience."""

import unittest


def aggregate_telemetry_metrics(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {"avg": 0.0, "max": 0.0, "min": 0.0}
    valid = [s for s in samples if isinstance(s, (int, float))]
    if not valid:
        return {"avg": 0.0, "max": 0.0, "min": 0.0}
    return {
        "avg": round(sum(valid) / len(valid), 2),
        "max": round(max(valid), 2),
        "min": round(min(valid), 2),
    }


class TestMetricsCollectorResilience(unittest.TestCase):
    def test_aggregate_telemetry_valid_samples(self):
        res = aggregate_telemetry_metrics([10.0, 20.0, 30.0])
        self.assertEqual(res["avg"], 20.0)
        self.assertEqual(res["max"], 30.0)
        self.assertEqual(res["min"], 10.0)

    def test_aggregate_telemetry_empty_samples(self):
        res = aggregate_telemetry_metrics([])
        self.assertEqual(res["avg"], 0.0)
        self.assertEqual(res["max"], 0.0)


if __name__ == "__main__":
    unittest.main()
