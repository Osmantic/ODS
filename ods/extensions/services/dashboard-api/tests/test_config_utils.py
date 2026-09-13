"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestMaskSensitiveConfig:
    def test_password_masked(self):
        from config import mask_sensitive_config
        assert mask_sensitive_config("db_password", "s3cr3t") == "***"

    def test_api_key_masked(self):
        from config import mask_sensitive_config
        assert mask_sensitive_config("API_KEY", "key123") == "***"

    def test_normal_key_not_masked(self):
        from config import mask_sensitive_config
        assert mask_sensitive_config("log_level", "debug") == "debug"

    def test_token_in_key_masked(self):
        from config import mask_sensitive_config
        assert mask_sensitive_config("bearer_token", "tok") == "***"

    def test_none_value_unmasked(self):
        from config import mask_sensitive_config
        assert mask_sensitive_config("host", None) == "None"
