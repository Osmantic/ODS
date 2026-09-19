"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestParseSemverTuple:
    def test_basic_semver(self):
        from config import parse_semver_tuple
        assert parse_semver_tuple("1.2.3") == (1, 2, 3)

    def test_v_prefix(self):
        from config import parse_semver_tuple
        assert parse_semver_tuple("v2.0.0") == (2, 0, 0)

    def test_two_part_returns_none(self):
        from config import parse_semver_tuple
        assert parse_semver_tuple("1.2") is None

    def test_non_numeric_returns_none(self):
        from config import parse_semver_tuple
        assert parse_semver_tuple("latest") is None

    def test_none_returns_none(self):
        from config import parse_semver_tuple
        assert parse_semver_tuple(None) is None
