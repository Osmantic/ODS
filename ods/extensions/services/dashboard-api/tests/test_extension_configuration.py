import asyncio
import json
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from host_agent_client import AgentHTTPError
from routers import extensions


def request(payload):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    async def receive():
        return {'type': 'http.request', 'body': raw, 'more_body': False}

    return Request({'type': 'http'}, receive)


def test_configuration_values_go_only_to_host_and_response_is_not_cached(monkeypatch):
    host = Mock(return_value={'service_id': 'demo', 'status': 'saved', 'saved_keys': ['DEMO_PASSWORD'],
                             'unexpected_raw_value': 'private'})
    monkeypatch.setattr(extensions, 'request_agent_json', host)
    response = asyncio.run(extensions.extension_configure('demo', request({'values': {'DEMO_PASSWORD': 'private'}}), 'test'))
    assert response.headers['cache-control'] == 'no-store'
    assert b'private' not in response.body
    host.assert_called_once_with('POST', '/v1/extensions/configure',
                                payload={'service_id': 'demo', 'values': {'DEMO_PASSWORD': 'private'}}, timeout=30)


@pytest.mark.parametrize('payload,code', [({'values': {'DEMO_PASSWORD': 1}}, 400),
    ({'values': {'DEMO_PASSWORD': 'private'}, 'other': 'private'}, 400),
    (b'{"values":"private"', 400), (b'x' * 15001, 413)])
def test_invalid_requests_never_echo_secrets_or_reach_host(monkeypatch, payload, code):
    host = Mock()
    monkeypatch.setattr(extensions, 'request_agent_json', host)
    with pytest.raises(HTTPException) as error:
        asyncio.run(extensions.extension_configure('demo', request(payload), 'test'))
    assert error.value.status_code == code
    assert 'private' not in str(error.value.detail)
    host.assert_not_called()


def test_host_error_is_redacted_and_not_retried(monkeypatch):
    host = Mock(side_effect=AgentHTTPError(409, 'private', 'private'))
    monkeypatch.setattr(extensions, 'request_agent_json', host)
    with pytest.raises(HTTPException) as error:
        asyncio.run(extensions.extension_configure('demo', request({'values': {'DEMO_PASSWORD': 'private'}}), 'test'))
    assert error.value.status_code == 409
    assert 'private' not in str(error.value.detail)
    assert host.call_count == 1


def test_unmatched_host_receipt_cannot_claim_success(monkeypatch):
    monkeypatch.setattr(extensions, 'request_agent_json', Mock(return_value={
        'service_id': 'other', 'status': 'saved', 'saved_keys': ['DEMO_PASSWORD']}))
    with pytest.raises(HTTPException) as error:
        asyncio.run(extensions.extension_configure('demo', request({'values': {'DEMO_PASSWORD': 'private'}}), 'test'))
    assert error.value.status_code == 502
