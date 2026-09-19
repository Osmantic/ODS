#!/usr/bin/env python3
"""Regression test: healthcheck._parse_target strips leading/trailing whitespace.

The baseline checked raw.startswith('http://') directly without stripping whitespace.
Targets read from environment files, command outputs, or user CLI input with whitespace
padding raised ValueError('target must be http(s) URL...').
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from healthcheck import _parse_target


class HealthcheckTargetWhitespaceTests(unittest.TestCase):
    def test_whitespace_padded_http_targets_parsed(self):
        for raw in (
            " http://127.0.0.1:8080 ",
            "\thttps://localhost:9090\n",
            "  http://[::1]:8000  ",
        ):
            kind, normalized = _parse_target(raw)
            self.assertEqual(kind, "http")
            self.assertEqual(normalized, raw.strip())

    def test_whitespace_padded_tcp_targets_parsed(self):
        for raw in (
            " tcp://127.0.0.1:5432 ",
            " 127.0.0.1:8000\n",
            " [::1]:8080 ",
        ):
            kind, normalized = _parse_target(raw)
            self.assertEqual(kind, "tcp")
            self.assertFalse(normalized.startswith(" ") or normalized.endswith(" "))


if __name__ == "__main__":
    unittest.main()
