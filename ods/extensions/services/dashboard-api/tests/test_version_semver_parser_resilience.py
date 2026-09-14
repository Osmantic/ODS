"""Unit test suite for Semantic Version (SemVer) parsing resilience."""

import unittest


def parse_semver_triple(ver_str: str | None) -> tuple[int, int, int]:
    if not ver_str or not isinstance(ver_str, str):
        return (0, 0, 0)
    cleaned = ver_str.strip().lstrip("v")
    parts = cleaned.split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2].split("-")[0]) if len(parts) > 2 else 0
        return (max(0, major), max(0, minor), max(0, patch))
    except (ValueError, IndexError):
        return (0, 0, 0)


class TestVersionSemverParserResilience(unittest.TestCase):
    def test_parse_semver_valid(self):
        self.assertEqual(parse_semver_triple("v1.2.3"), (1, 2, 3))
        self.assertEqual(parse_semver_triple("2.0.1-beta"), (2, 0, 1))

    def test_parse_semver_invalid(self):
        self.assertEqual(parse_semver_triple("invalid_ver"), (0, 0, 0))
        self.assertEqual(parse_semver_triple(None), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
