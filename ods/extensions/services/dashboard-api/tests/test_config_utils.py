"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestSanitizeEnvKey:
    def test_already_valid(self):
        from config import sanitize_env_key
        assert sanitize_env_key("ODS_MODE") == "ODS_MODE"

    def test_lowercase_converted(self):
        from config import sanitize_env_key
        assert sanitize_env_key("ods_mode") == "ODS_MODE"

    def test_dashes_replaced(self):
        from config import sanitize_env_key
        assert sanitize_env_key("my-key") == "MY_KEY"

    def test_leading_trailing_special(self):
        from config import sanitize_env_key
        assert sanitize_env_key("__ods__") == "ODS"

    def test_empty_returns_empty(self):
        from config import sanitize_env_key
        assert sanitize_env_key("") == ""

    def test_none_returns_empty(self):
        from config import sanitize_env_key
        assert sanitize_env_key(None) == ""
