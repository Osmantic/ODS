"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


class TestResolveBoolEnv:
    def test_true_string(self, monkeypatch):
        from config import resolve_bool_env
        monkeypatch.setenv("TEST_FLAG", "true")
        assert resolve_bool_env("TEST_FLAG") is True

    def test_false_string(self, monkeypatch):
        from config import resolve_bool_env
        monkeypatch.setenv("TEST_FLAG", "false")
        assert resolve_bool_env("TEST_FLAG") is False

    def test_yes_truthy(self, monkeypatch):
        from config import resolve_bool_env
        monkeypatch.setenv("TEST_FLAG", "YES")
        assert resolve_bool_env("TEST_FLAG") is True

    def test_missing_returns_default(self, monkeypatch):
        from config import resolve_bool_env
        monkeypatch.delenv("TEST_FLAG", raising=False)
        assert resolve_bool_env("TEST_FLAG", default=True) is True

    def test_garbage_returns_default(self, monkeypatch):
        from config import resolve_bool_env
        monkeypatch.setenv("TEST_FLAG", "maybe")
        assert resolve_bool_env("TEST_FLAG") is False
