"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


import os
from pathlib import Path

class TestResolveDataDir:
    def test_home_expansion(self):
        from config import resolve_data_dir
        result = resolve_data_dir("~/ods")
        assert result is not None
        assert result.is_absolute()

    def test_none_returns_none(self):
        from config import resolve_data_dir
        assert resolve_data_dir(None) is None

    def test_empty_returns_none(self):
        from config import resolve_data_dir
        assert resolve_data_dir("") is None

    def test_must_exist_nonexistent(self):
        from config import resolve_data_dir
        result = resolve_data_dir("/definitely/does/not/exist/xyz123", must_exist=True)
        assert result is None

    def test_must_exist_existing(self, tmp_path):
        from config import resolve_data_dir
        result = resolve_data_dir(str(tmp_path), must_exist=True)
        assert result == tmp_path.resolve()
