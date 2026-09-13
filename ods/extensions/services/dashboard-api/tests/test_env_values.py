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


def test_parse_env_duration_seconds():
    from env_values import parse_env_duration_seconds
    assert parse_env_duration_seconds("30s") == 30
    assert parse_env_duration_seconds("5m") == 300
    assert parse_env_duration_seconds("2h") == 7200
    assert parse_env_duration_seconds("1d") == 86400
    assert parse_env_duration_seconds("42") == 42
    assert parse_env_duration_seconds("bad", default=10) == 10
