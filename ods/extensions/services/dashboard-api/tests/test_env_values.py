import pytest

from env_values import strip_matching_quotes


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("value", "value"),
        ("  value  ", "value"),
        ('"value"', "value"),
        ("'value'", "value"),
        ('""', ""),
        ("''", ""),
        ("it's", "it's"),
        ('"value', '"value'),
        ('value"', 'value"'),
        ("'value", "'value"),
        ("value'", "value'"),
        ('"value\'', '"value\''),
        ("''value''", "'value'"),
        ('""value""', '"value"'),
    ],
)
def test_strip_matching_quotes_removes_exactly_one_complete_pair(raw, expected):
    assert strip_matching_quotes(raw) == expected


def test_env_default_if_whitespace():
    from env_values import env_default_if_whitespace
    assert env_default_if_whitespace("actual", "fallback") == "actual"
    assert env_default_if_whitespace("   ", "fallback") == "fallback"
    assert env_default_if_whitespace("", "fallback") == "fallback"
    assert env_default_if_whitespace(None, "fallback") == "fallback"
