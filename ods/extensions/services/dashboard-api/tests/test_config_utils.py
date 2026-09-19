"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestValidateJsonConfig:
    def test_valid_json_object(self):
        from config import validate_json_config
        result = validate_json_config('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_array_returns_none(self):
        from config import validate_json_config
        assert validate_json_config('[1, 2, 3]') is None

    def test_invalid_json_returns_none(self):
        from config import validate_json_config
        assert validate_json_config("{bad json}") is None

    def test_none_returns_none(self):
        from config import validate_json_config
        assert validate_json_config(None) is None

    def test_empty_returns_none(self):
        from config import validate_json_config
        assert validate_json_config("") is None
