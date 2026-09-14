"""Unit test suite for ISO-8601 timestamp string parsing resilience."""

import unittest
from datetime import datetime, timezone


def parse_iso_timestamp_utc(ts_str: str | None) -> datetime | None:
    if not ts_str or not isinstance(ts_str, str):
        return None
    cleaned = ts_str.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


class TestISOTimestampParserResilience(unittest.TestCase):
    def test_parse_iso_timestamp_valid_z(self):
        dt = parse_iso_timestamp_utc("2026-08-29T12:00:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)

    def test_parse_iso_timestamp_invalid(self):
        self.assertIsNone(parse_iso_timestamp_utc("invalid_date"))
        self.assertIsNone(parse_iso_timestamp_utc(None))


if __name__ == "__main__":
    unittest.main()
