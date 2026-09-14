"""Unit test suite for JSON file serialization and indentation resilience."""

import json
import unittest


def serialize_json_pretty(data: object, indent: int = 2) -> str | None:
    if data is None:
        return None
    try:
        return json.dumps(data, indent=max(0, indent), ensure_ascii=False)
    except (TypeError, ValueError):
        return None


class TestJSONFileWriterResilience(unittest.TestCase):
    def test_serialize_json_valid(self):
        res = serialize_json_pretty({"status": "ok"})
        self.assertIsNotNone(res)
        self.assertIn('"status": "ok"', res)

    def test_serialize_json_none(self):
        self.assertIsNone(serialize_json_pretty(None))


if __name__ == "__main__":
    unittest.main()
