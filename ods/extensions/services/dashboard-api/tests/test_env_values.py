import pytest

from env_values import parse_env_value, quote_env_value, strip_matching_quotes


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Bare values every reader agrees on stay bare.
        ("", ""),
        ("value", "value"),
        ("sk-live-secret", "sk-live-secret"),
        ("http://llama-server:8080/v1", "http://llama-server:8080/v1"),
        ("it's", "it's"),
        ("a=b", "a=b"),
        ('say "hi"', 'say "hi"'),
        ("C:\\models\\foo.gguf", "C:\\models\\foo.gguf"),
        # Compose interpolates $NAME, cuts unquoted values at " #", trims
        # surrounding whitespace and treats a leading quote as a delimiter.
        ("hunter$two", "'hunter$two'"),
        ("abc${", "'abc${'"),
        ("token #1", "'token #1'"),
        ("#literal", "'#literal'"),
        ("  padded  ", "'  padded  '"),
        ('"already"', "'\"already\"'"),
        ("'x", "\"'x\""),
        # A single quote or a backslash needs the double-quoted escape set.
        ("it's $5", '"it\'s \\$5"'),
        ('"x" it\'s', '"\\"x\\" it\'s"'),
        ("back\\slash $x", '"back\\\\slash \\$x"'),
    ],
)
def test_quote_env_value_only_quotes_when_readers_would_disagree(value, expected):
    assert quote_env_value(value) == expected


@pytest.mark.parametrize("value", ["a\nb", "a\rb"])
def test_quote_env_value_rejects_newlines(value):
    with pytest.raises(ValueError):
        quote_env_value(value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Rows taken from `docker compose config` on the same lines.
        ("11434          # llama-server API", "11434"),
        ("a  # x", "a"),
        ("a# b", "a# b"),
        ("  # only comment", "# only comment"),
        (" #x", "#x"),
        ("   padded value   ", "padded value"),
        ("   ", ""),
        ('"quoted value" # trailing', "quoted value"),
        ("'single value'   # trailing", "single value"),
        ('"keep # this"', "keep # this"),
        ("'keep # this too'", "keep # this too"),
        ("\"'literal'\"", "'literal'"),
        ("'\"json\"'", '"json"'),
        ('say "hi"', 'say "hi"'),
        ('trailing-quote"', 'trailing-quote"'),
        ('  "leading space quote"', "leading space quote"),
        ('"q" ', "q"),
        # The writer's escape set decodes back to the original value.
        ('"it\'s \\$5"', "it's $5"),
        ('"say \\"hi\\" it\'s"', 'say "hi" it\'s'),
        ('"back\\\\slash \\$x"', "back\\slash $x"),
        # Mismatched quotes stay data (strip_matching_quotes fallback).
        ("''value''", "'value'"),
        ('"value', '"value'),
    ],
)
def test_parse_env_value_matches_compose(raw, expected):
    assert parse_env_value(raw) == expected


@pytest.mark.parametrize(
    "value",
    ["plain", "hunter$two", "token #1", "it's $5", 'say "hi" it\'s', "back\\slash $x", "  padded  ", "#literal", "'x"],
)
def test_quote_then_parse_round_trips(value):
    assert parse_env_value(quote_env_value(value)) == value
