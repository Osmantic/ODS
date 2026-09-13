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


def test_parse_env_ip_address():
    from env_values import parse_env_ip_address
    assert parse_env_ip_address("192.168.1.1") == "192.168.1.1"
    assert parse_env_ip_address("256.0.0.1") == "127.0.0.1"
    assert parse_env_ip_address("not_an_ip", default="0.0.0.0") == "0.0.0.0"
    assert parse_env_ip_address("") == "127.0.0.1"
