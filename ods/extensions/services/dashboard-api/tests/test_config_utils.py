"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestCoercePortInt:
    def test_valid_int(self):
        from config import coerce_port_int
        assert coerce_port_int(3000) == 3000

    def test_valid_string(self):
        from config import coerce_port_int
        assert coerce_port_int("8443") == 8443

    def test_out_of_range(self):
        from config import coerce_port_int
        assert coerce_port_int(99999) == 8080

    def test_none_uses_default(self):
        from config import coerce_port_int
        assert coerce_port_int(None, default=443) == 443

    def test_garbage_string(self):
        from config import coerce_port_int
        assert coerce_port_int("abc") == 8080
