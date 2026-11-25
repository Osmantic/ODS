"""The host agent's Hermes model-block reader (CodeQL #319).

The key is matched with an unambiguous pattern and the value is stripped in
Python; these tests pin the values it reads.
"""
import pytest

from test_host_agent import _mod

TEMPLATE = (
    "model:\n"
    "  default: {default}\n"
    "  base_url: {base_url}\n"
    "  context_length: {context}\n"
    "auxiliary:\n"
    "  compression:\n"
    "    context_length: 8192\n"
)


@pytest.mark.parametrize("default, base_url, context", [
    ('"ods/current"', "http://litellm:4000/v1", "65536"),
    ("'ods/current'   ", "  http://litellm:4000/v1\t", " 65536 "),
    ('"ods/current"  # pinned', "http://litellm:4000/v1 # gateway", "65536 # tokens"),
])
def test_model_block_values_ignore_quotes_comments_and_padding(default, base_url, context):
    text = TEMPLATE.format(default=default, base_url=base_url, context=context)
    assert _mod._hermes_config_matches(text, "ods/current", "http://litellm:4000/v1", 65536)
    assert not _mod._hermes_config_matches(text, "ods/other", "http://litellm:4000/v1", 65536)
    assert not _mod._hermes_config_matches(text, "ods/current", "http://litellm:4000/v1", 32768)


def test_auxiliary_context_length_is_not_the_model_context():
    text = TEMPLATE.format(default='"ods/current"', base_url="http://litellm:4000/v1", context="8192")
    assert not _mod._hermes_config_matches(text, "ods/current", "http://litellm:4000/v1", 65536)

