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


def test_env_is_truthy_loose():
    from env_values import env_is_truthy_loose
    assert env_is_truthy_loose(" Yes ") is True
    assert env_is_truthy_loose("1") is True
    assert env_is_truthy_loose("enabled") is True
    assert env_is_truthy_loose("false") is False
    assert env_is_truthy_loose("") is False
