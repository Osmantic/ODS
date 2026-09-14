"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestStripTrailingSlash:
    def test_single_slash(self):
        from config import strip_trailing_slash
        assert strip_trailing_slash("http://example.com/") == "http://example.com"

    def test_multiple_slashes(self):
        from config import strip_trailing_slash
        assert strip_trailing_slash("http://example.com///") == "http://example.com"

    def test_no_slash(self):
        from config import strip_trailing_slash
        assert strip_trailing_slash("http://example.com") == "http://example.com"

    def test_none_returns_empty(self):
        from config import strip_trailing_slash
        assert strip_trailing_slash(None) == ""

    def test_path_slash_preserved(self):
        from config import strip_trailing_slash
        assert strip_trailing_slash("http://x.com/api/") == "http://x.com/api"
