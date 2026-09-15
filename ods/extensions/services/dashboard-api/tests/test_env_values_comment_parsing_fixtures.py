import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from env_values import parse_env_value, quote_env_value

def test_parse_tab_separated_comment():
    assert parse_env_value("PORT=8080\t# comment") == "PORT=8080"
    assert parse_env_value("HOST=localhost    # inline comment") == "HOST=localhost"

def test_parse_quoted_hash_preserved():
    assert parse_env_value('"value # not a comment"') == "value # not a comment"
    assert parse_env_value("'value # not a comment'") == "value # not a comment"

def test_quote_env_value_roundtrip():
    val = "simple_value"
    assert quote_env_value(val) == val
    quoted = quote_env_value("value with spaces")
    assert parse_env_value(quoted) == "value with spaces"
