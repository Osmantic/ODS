"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestParseCommaList:
    def test_basic_split(self):
        from config import parse_comma_list
        assert parse_comma_list("a,b,c") == ["a", "b", "c"]

    def test_whitespace_stripped(self):
        from config import parse_comma_list
        assert parse_comma_list(" a , b , c ") == ["a", "b", "c"]

    def test_dedup_default(self):
        from config import parse_comma_list
        assert parse_comma_list("a,b,a,c") == ["a", "b", "c"]

    def test_dedup_disabled(self):
        from config import parse_comma_list
        assert parse_comma_list("a,b,a", unique=False) == ["a", "b", "a"]

    def test_empty_tokens_skipped(self):
        from config import parse_comma_list
        assert parse_comma_list("a,,b") == ["a", "b"]

    def test_none_returns_empty(self):
        from config import parse_comma_list
        assert parse_comma_list(None) == []
