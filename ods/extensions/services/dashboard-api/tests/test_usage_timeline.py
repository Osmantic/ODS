import unittest
from datetime import datetime, timezone
from usage_timeline import minute_timeline


class TimelineTests(unittest.TestCase):
    def test_minute_buckets_preserve_totals_and_do_not_expose_content(self):
        result = minute_timeline([
            {"timestamp":"2026-09-08T00:01:01Z", "input_tokens":100, "output_tokens":20, "prompt":"private"},
            {"timestamp":"2026-09-08 00:01:50", "input_tokens":50, "output_tokens":10},
            {"timestamp":"2026-09-08T00:03:00+00:00", "input_tokens":5, "output_tokens":2},
        ], datetime(2026,9,8,0,4,tzinfo=timezone.utc))
        self.assertEqual([p["requests"] for p in result["points"]], [0,2,0,1,0])
        self.assertEqual(result["points"][1]["input_tokens"],150)
        self.assertEqual(sum(p["total_tokens"] for p in result["points"]),187)
        self.assertNotIn("private", str(result))

    def test_date_boundaries_invalid_records_and_empty_day(self):
        result = minute_timeline([
            {"timestamp":"2026-09-07T23:59:59Z", "input_tokens":8,"output_tokens":2},
            {"timestamp":"2026-09-08T12:00:00Z", "input_tokens":8,"output_tokens":2},
            {"timestamp":"bad"},
            {"timestamp":"2026-09-08T00:00:00Z", "input_tokens":-1,"output_tokens":2},
        ], datetime(2026,9,8,0,1,tzinfo=timezone.utc))
        self.assertEqual(result["recorded_requests"],0)
        self.assertEqual(result["invalid_records"],2)
        self.assertEqual(len(result["points"]),2)

    def test_capped_history_starts_at_first_observed_minute(self):
        from unittest.mock import patch
        with patch('usage_timeline.MAX_EVENTS',1):
            result = minute_timeline([{"timestamp":"2026-09-08T00:03:15Z","input_tokens":8,"output_tokens":2}],datetime(2026,9,8,0,4,tzinfo=timezone.utc))
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["points"]),2)


if __name__ == '__main__':
    unittest.main()
