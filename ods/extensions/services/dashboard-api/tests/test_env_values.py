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


def test_env_require_non_empty():
    from env_values import env_require_non_empty
    assert env_require_non_empty(" token ", "API_TOKEN") == "token"
    import pytest
    with pytest.raises(ValueError, match="API_TOKEN cannot be empty"):
        env_require_non_empty("   ", "API_TOKEN")
    with pytest.raises(ValueError):
        env_require_non_empty("", "API_TOKEN")
