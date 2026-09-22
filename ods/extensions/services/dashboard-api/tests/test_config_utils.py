"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


import pytest

class TestRequireEnvVar:
    def test_present_var(self, monkeypatch):
        from config import require_env_var
        monkeypatch.setenv("MY_REQUIRED_VAR", "hello")
        assert require_env_var("MY_REQUIRED_VAR") == "hello"

    def test_missing_var_raises(self, monkeypatch):
        from config import require_env_var
        monkeypatch.delenv("MY_REQUIRED_VAR", raising=False)
        with pytest.raises(RuntimeError, match="MY_REQUIRED_VAR"):
            require_env_var("MY_REQUIRED_VAR")

    def test_empty_var_raises(self, monkeypatch):
        from config import require_env_var
        monkeypatch.setenv("MY_REQUIRED_VAR", "   ")
        with pytest.raises(RuntimeError, match="MY_REQUIRED_VAR"):
            require_env_var("MY_REQUIRED_VAR")

    def test_error_message_descriptive(self, monkeypatch):
        from config import require_env_var
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="deployment configuration"):
            require_env_var("SECRET_KEY")
