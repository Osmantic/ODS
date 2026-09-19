"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestCoerceMemoryBytes:
    def test_gigabytes(self):
        from config import coerce_memory_bytes
        assert coerce_memory_bytes("4GB") == 4 * 1024 ** 3

    def test_megabytes_with_space(self):
        from config import coerce_memory_bytes
        assert coerce_memory_bytes("512 MB") == 512 * 1024 ** 2

    def test_bare_number_as_bytes(self):
        from config import coerce_memory_bytes
        assert coerce_memory_bytes("1024") == 1024

    def test_none_returns_none(self):
        from config import coerce_memory_bytes
        assert coerce_memory_bytes(None) is None

    def test_unknown_suffix_returns_none(self):
        from config import coerce_memory_bytes
        assert coerce_memory_bytes("4 ZB") is None
