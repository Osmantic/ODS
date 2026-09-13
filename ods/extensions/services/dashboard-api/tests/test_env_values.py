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


def test_parse_env_byte_size():
    from env_values import parse_env_byte_size
    assert parse_env_byte_size("1024") == 1024
    assert parse_env_byte_size("1K") == 1024
    assert parse_env_byte_size("2MB") == 2097152
    assert parse_env_byte_size("1G") == 1073741824
    assert parse_env_byte_size("invalid", default=-1) == -1
