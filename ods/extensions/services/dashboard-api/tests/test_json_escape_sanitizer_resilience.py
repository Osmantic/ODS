"""Unit test suite for JSON control character escaping resilience."""

import json
import unittest


def escape_json_control_chars(text: str | None) -> str:
    if text is None:
        return ""
    s = str(text)
    return json.dumps(s)[1:-1]


class TestJSONEscapeSanitizerResilience(unittest.TestCase):
    def test_escape_json_valid(self):
        self.assertEqual(escape_json_control_chars('hello "world"\n'), 'hello \\"world\\"\\n')

    def test_escape_json_none(self):
        self.assertEqual(escape_json_control_chars(None), "")


if __name__ == "__main__":
    unittest.main()
