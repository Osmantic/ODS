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


def test_parse_env_list_clean():
    from env_values import parse_env_list_clean
    assert parse_env_list_clean("a, b , c") == ["a", "b", "c"]
    assert parse_env_list_clean("a,,c, ") == ["a", "c"]
    assert parse_env_list_clean("x;y;z", sep=";") == ["x", "y", "z"]
    assert parse_env_list_clean("") == []
