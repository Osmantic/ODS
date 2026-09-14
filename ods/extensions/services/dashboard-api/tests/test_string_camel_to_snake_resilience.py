"""Unit test suite for camelCase to snake_case conversion resilience."""

import re
import unittest

_CAMEL_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")


def camel_to_snake_case(camel_str: str | None) -> str:
    if not camel_str or not isinstance(camel_str, str):
        return ""
    cleaned = camel_str.strip()
    return _CAMEL_CASE_RE.sub("_", cleaned).lower()


class TestStringCamelToSnakeResilience(unittest.TestCase):
    def test_camel_to_snake_valid(self):
        self.assertEqual(camel_to_snake_case("serverHostName"), "server_host_name")

    def test_camel_to_snake_none(self):
        self.assertEqual(camel_to_snake_case(None), "")


if __name__ == "__main__":
    unittest.main()
