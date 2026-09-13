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


def test_env_mask_secret():
    from env_values import env_mask_secret
    assert env_mask_secret("supersecrettoken1234") == "****************1234"
    assert env_mask_secret("short") == "short".replace("short", "*****")
    assert env_mask_secret("abcdef", visible_chars=2) == "****ef"
    assert env_mask_secret("") == ""
