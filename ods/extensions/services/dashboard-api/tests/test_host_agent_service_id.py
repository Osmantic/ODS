"""The host agent accepts only whole, plain extension ids.

A service id becomes a folder name (user-extensions, progress files) and a
Compose argument. ``$`` also matches before a final newline, so with ``.match``
an id like ``"n8n\\n"`` used to pass every check; the pattern now ends in
``\\Z``, as dashboard-api's does.
"""
import json

import pytest

from test_host_agent import _FakeHandler, _mod


@pytest.mark.parametrize("service_id", ["n8n\n", "n8n\r\n", "\nn8n", "../n8n", "-n8n", "N8N", ""])
def test_service_id_pattern_refuses_anything_but_a_plain_id(service_id):
    assert _mod.SERVICE_ID_RE.match(service_id) is None
    assert _mod.SERVICE_ID_RE.fullmatch(service_id) is None


@pytest.mark.parametrize("service_id", ["n8n", "hermes-proxy", "token_spy", "0x"])
def test_service_id_pattern_accepts_plain_ids(service_id):
    assert _mod.SERVICE_ID_RE.fullmatch(service_id)


def test_validate_service_id_refuses_a_trailing_newline():
    handler = _FakeHandler(b"")

    assert _mod.validate_service_id(handler, {"service_id": "n8n\n"}) is None
    assert handler.response_code == 400
    assert json.loads(handler.wfile.getvalue()) == {"error": "Invalid service_id"}
