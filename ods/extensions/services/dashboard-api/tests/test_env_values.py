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


class TestParseEnvListSafe:

    def test_parses_delimited_lists(self):
        from env_values import parse_env_list_safe
        assert parse_env_list_safe("a, b, c") == ["a", "b", "c"]
        assert parse_env_list_safe("x; y; z", delimiter=";") == ["x", "y", "z"]

    def test_handles_none_and_empty_inputs(self):
        from env_values import parse_env_list_safe
        assert parse_env_list_safe(None) == []
        assert parse_env_list_safe("  ") == []

