"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestClampTimeout:
    def test_normal_value(self):
        from config import clamp_timeout
        assert clamp_timeout(60) == 60.0

    def test_too_large_clamped(self):
        from config import clamp_timeout
        assert clamp_timeout(9999) == 3600.0

    def test_zero_returns_default(self):
        from config import clamp_timeout
        assert clamp_timeout(0) == 30.0

    def test_negative_returns_default(self):
        from config import clamp_timeout
        assert clamp_timeout(-5) == 30.0

    def test_string_numeric(self):
        from config import clamp_timeout
        assert clamp_timeout("120") == 120.0

    def test_none_returns_default(self):
        from config import clamp_timeout
        assert clamp_timeout(None, default=10.0) == 10.0
