"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


import pytest

class TestFlattenConfigKeys:
    def test_flat_dict_unchanged(self):
        from config import flatten_config_keys
        assert flatten_config_keys({"a": 1, "b": 2}) == {"a": 1, "b": 2}

    def test_nested_dict_flattened(self):
        from config import flatten_config_keys
        result = flatten_config_keys({"db": {"host": "localhost", "port": 5432}})
        assert result == {"db.host": "localhost", "db.port": 5432}

    def test_custom_separator(self):
        from config import flatten_config_keys
        result = flatten_config_keys({"a": {"b": 1}}, sep="/")
        assert "a/b" in result

    def test_empty_dict_returns_empty(self):
        from config import flatten_config_keys
        assert flatten_config_keys({}) == {}

    def test_non_dict_raises(self):
        from config import flatten_config_keys
        with pytest.raises(TypeError):
            flatten_config_keys([1, 2, 3])
