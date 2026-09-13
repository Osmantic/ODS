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


def test_parse_env_float_clamped():
    from env_values import parse_env_float_clamped
    assert parse_env_float_clamped("3.14", 0.0, 5.0) == 3.14
    assert parse_env_float_clamped("10.5", 0.0, 5.0) == 5.0
    assert parse_env_float_clamped("-2.0", 0.0, 5.0) == 0.0
    assert parse_env_float_clamped("bad", 0.0, 5.0, default=1.5) == 1.5
