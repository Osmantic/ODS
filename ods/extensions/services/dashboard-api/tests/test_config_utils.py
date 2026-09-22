"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestValidateUrlScheme:
    def test_https_valid(self):
        from config import validate_url_scheme
        assert validate_url_scheme("https://example.com") is True

    def test_grpc_valid(self):
        from config import validate_url_scheme
        assert validate_url_scheme("grpc://localhost:50051") is True

    def test_ftp_invalid(self):
        from config import validate_url_scheme
        assert validate_url_scheme("ftp://files.example.com") is False

    def test_no_scheme(self):
        from config import validate_url_scheme
        assert validate_url_scheme("example.com") is False

    def test_none_returns_false(self):
        from config import validate_url_scheme
        assert validate_url_scheme(None) is False

    def test_custom_allowed(self):
        from config import validate_url_scheme
        assert validate_url_scheme("ftp://x.com", allowed=frozenset({"ftp"})) is True
