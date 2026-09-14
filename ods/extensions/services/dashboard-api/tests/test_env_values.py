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


class TestParseEnvFloatSafe:

    def test_parses_float_values(self):
        from env_values import parse_env_float_safe
        assert parse_env_float_safe("3.14") == 3.14
        assert parse_env_float_safe(2.5) == 2.5

    def test_handles_none_and_bounds_clamping(self):
        from env_values import parse_env_float_safe
        assert parse_env_float_safe(None, default=1.0) == 1.0
        assert parse_env_float_safe("nan", default=1.0) == 1.0
        assert parse_env_float_safe("0.5", min_val=1.0) == 1.0
        assert parse_env_float_safe("10.0", max_val=5.0) == 5.0

