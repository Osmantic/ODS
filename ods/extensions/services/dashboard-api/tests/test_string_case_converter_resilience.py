"""Unit test suite for snake_case to camelCase conversion resilience."""

import re
import unittest

_SNAKE_CASE_RE = re.compile(r"_([a-z])")


def snake_to_camel_case(snake_str: str | None) -> str:
    if not snake_str or not isinstance(snake_str, str):
        return ""
    cleaned = snake_str.strip()
    return _SNAKE_CASE_RE.sub(lambda m: m.group(1).upper(), cleaned)


class TestStringCaseConverterResilience(unittest.TestCase):
    def test_snake_to_camel_valid(self):
        self.assertEqual(snake_to_camel_case("server_host_name"), "serverHostName")

    def test_snake_to_camel_none(self):
        self.assertEqual(snake_to_camel_case(None), "")


if __name__ == "__main__":
    unittest.main()
