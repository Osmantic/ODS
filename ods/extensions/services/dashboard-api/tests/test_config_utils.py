"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


import pytest

class TestTruncateLabel:
    def test_short_label_unchanged(self):
        from config import truncate_label
        assert truncate_label("hello", max_len=10) == "hello"

    def test_long_label_truncated(self):
        from config import truncate_label
        result = truncate_label("a" * 100, max_len=10)
        assert len(result) == 10
        assert result.endswith("…")

    def test_exact_length_unchanged(self):
        from config import truncate_label
        assert truncate_label("hello", max_len=5) == "hello"

    def test_none_returns_empty(self):
        from config import truncate_label
        assert truncate_label(None) == ""

    def test_zero_max_len_raises(self):
        from config import truncate_label
        with pytest.raises(ValueError):
            truncate_label("test", max_len=0)
