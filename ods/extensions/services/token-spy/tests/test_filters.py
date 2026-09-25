"""Unit tests for Token Spy request filters non-dict and type bounds guards."""

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent

def _load_filters_module():
    filters_path = TOKEN_SPY_DIR / "filters.py"
    spec = importlib.util.spec_from_file_location(
        f"filters_{uuid4().hex}", filters_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

filters_mod = _load_filters_module()
apply_filters = filters_mod.apply_filters
FilterResult = filters_mod.FilterResult
_group_into_units = filters_mod._group_into_units


def test_apply_filters_with_non_dict_tool_entries():
    """Verify that non-dict entries in tools list do not raise AttributeError."""
    body = {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [None, "invalid_str", {"type": "function", "function": {"name": "search_db"}}],
    }
    filter_settings = {
        "enabled": True,
        "tools": {
            "enabled": True,
            "mode": "blocklist",
            "blocklist": ["search_db"],
        },
    }
    filtered_body, result = apply_filters(body, filter_settings)
    assert isinstance(result, FilterResult)
    assert result.tools_removed == 1


def test_group_into_units_with_non_dict_messages():
    """Verify that non-dict items in messages list are safely skipped."""
    messages = [
        None,
        "string message",
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello!"},
    ]
    units = _group_into_units(messages)
    assert len(units) == 1
    assert len(units[0]) == 2
    assert units[0][0]["role"] == "user"
