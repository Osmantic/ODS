"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestExtractHostPort:
    def test_host_and_port(self):
        from config import extract_host_port
        assert extract_host_port("localhost:8080") == ("localhost", 8080)

    def test_host_only(self):
        from config import extract_host_port
        assert extract_host_port("localhost") == ("localhost", None)

    def test_ipv6(self):
        from config import extract_host_port
        assert extract_host_port("[::1]:9000") == ("::1", 9000)

    def test_none_returns_empty(self):
        from config import extract_host_port
        assert extract_host_port(None) == ("", None)

    def test_invalid_port(self):
        from config import extract_host_port
        assert extract_host_port("host:abc") == ("host", None)
