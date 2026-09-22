"""Manifest semver parsing must handle v/V prefixes and whitespace gracefully."""

import unittest


def parse_version(v: str):
    """Normalized parse_version implementation under test."""
    v = str(v).strip().lstrip("vV")
    parts = []
    for part in v.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


class ManifestSemverPrefixTests(unittest.TestCase):
    def test_prefixed_versions_parse_correctly(self):
        cases = [
            ("v2.1.0", (2, 1, 0)),
            ("V3.0.0", (3, 0, 0)),
            ("  v1.4.2  ", (1, 4, 2)),
            ("2.0.0", (2, 0, 0)),
            ("v2", (2, 0, 0)),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(parse_version(raw), expected)

    def test_unnormalized_baseline_fails_prefix(self):
        # Demonstrates the baseline defect where 'v2' turns into 0
        raw_parts = []
        for part in "v2.1.0".split("."):
            try:
                raw_parts.append(int(part))
            except ValueError:
                raw_parts.append(0)
        self.assertEqual(tuple(raw_parts[:3]), (0, 1, 0))  # Baseline bug


if __name__ == "__main__":
    unittest.main()
