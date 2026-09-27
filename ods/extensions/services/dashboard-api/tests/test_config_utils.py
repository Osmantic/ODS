"""Unit tests for config.py utility guards — Vishaaallll contributions."""
import os
import pytest


import pytest

class TestMergeConfigDicts:
    def test_flat_override(self):
        from config import merge_config_dicts
        result = merge_config_dicts({"a": 1, "b": 2}, {"b": 99})
        assert result == {"a": 1, "b": 99}

    def test_nested_merge(self):
        from config import merge_config_dicts
        base     = {"db": {"host": "localhost", "port": 5432}}
        override = {"db": {"port": 5433}}
        assert merge_config_dicts(base, override) == {"db": {"host": "localhost", "port": 5433}}

    def test_no_mutation_of_base(self):
        from config import merge_config_dicts
        base = {"a": 1}
        merge_config_dicts(base, {"a": 2})
        assert base == {"a": 1}

    def test_non_dict_base_raises(self):
        from config import merge_config_dicts
        with pytest.raises(TypeError):
            merge_config_dicts([], {"a": 1})

    def test_new_key_added(self):
        from config import merge_config_dicts
        result = merge_config_dicts({"a": 1}, {"b": 2})
        assert result["b"] == 2
