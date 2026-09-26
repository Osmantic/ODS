"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


import logging

class TestNormalizeLogLevel:
    def test_debug_string(self):
        from config import normalize_log_level
        assert normalize_log_level("DEBUG") == logging.DEBUG

    def test_warn_alias(self):
        from config import normalize_log_level
        assert normalize_log_level("warn") == logging.WARNING

    def test_fatal_alias(self):
        from config import normalize_log_level
        assert normalize_log_level("FATAL") == logging.CRITICAL

    def test_unknown_returns_default(self):
        from config import normalize_log_level
        assert normalize_log_level("verbose") == logging.INFO

    def test_none_returns_default(self):
        from config import normalize_log_level
        assert normalize_log_level(None) == logging.INFO

    def test_custom_default(self):
        from config import normalize_log_level
        assert normalize_log_level("", default=logging.ERROR) == logging.ERROR
