import asyncio
import json
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from host_agent_client import AgentHTTPError
from routers import extensions


HEX_KEY = 'a' * 64
DEMO = {'id': 'demo', 'name': 'Demo', 'env_vars': [
    {'key': 'DEMO_PASSWORD', 'required': True, 'secret': True},
    {'key': 'DEMO_DB_PASSWORD', 'required': True, 'secret': True, 'format': 'hex64', 'generate': 'hex64'},
    {'key': 'DEMO_ADMIN_PASSWORD', 'required': True, 'secret': True, 'min_length': 12, 'max_length': 72},
    {'key': 'DEMO_API_KEY', 'required': True, 'secret': True, 'format': 'hex64',
     'distinct_from': ['DEMO_DB_PASSWORD']},
]}


SAVED = {}


@pytest.fixture(autouse=True)
def demo_definition(monkeypatch):
    # The endpoint checks declared formats with the install plan's lookup.
    monkeypatch.setattr(extensions, '_installation_plan_service',
                        lambda service_id: DEMO if service_id == 'demo' else (_ for _ in ()).throw(ValueError()))
    import config
    SAVED.clear()
    monkeypatch.setattr(config, '_read_env_value', lambda key: SAVED.get(key, ''))


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


@pytest.mark.parametrize('values,keys,expected', [
    ({'DEMO_DB_PASSWORD': 'private-not-hex'}, ['DEMO_DB_PASSWORD'],
     'DEMO_DB_PASSWORD must be 64 hexadecimal characters (0-9, a-f). Nothing was saved.'),
    ({'DEMO_DB_PASSWORD': 'G' * 64}, ['DEMO_DB_PASSWORD'], None),
    ({'DEMO_DB_PASSWORD': HEX_KEY + ' '}, ['DEMO_DB_PASSWORD'], None),
    ({'DEMO_ADMIN_PASSWORD': 'private'}, ['DEMO_ADMIN_PASSWORD'],
     'DEMO_ADMIN_PASSWORD must be at least 12 characters, no more than 72 bytes. Nothing was saved.'),
    # 70 characters but 140 bytes: over a 72-byte limit such as bcrypt's.
    ({'DEMO_ADMIN_PASSWORD': 'é' * 70}, ['DEMO_ADMIN_PASSWORD'], None),
    ({'DEMO_DB_PASSWORD': 'private', 'DEMO_ADMIN_PASSWORD': 'private', 'DEMO_PASSWORD': 'private'},
     ['DEMO_ADMIN_PASSWORD', 'DEMO_DB_PASSWORD'], None),
])
def test_values_outside_their_declared_format_are_refused_before_the_host(monkeypatch, values, keys, expected):
    host = Mock()
    monkeypatch.setattr(extensions, 'request_agent_json', host)
    with pytest.raises(HTTPException) as error:
        asyncio.run(extensions.extension_configure('demo', request({'values': values}), 'test'))
    assert error.value.status_code == 422
    detail = error.value.detail
    assert detail['code'] == 'invalid_configuration' and detail['service_id'] == 'demo'
    assert [item['key'] for item in detail['invalid_configuration']] == keys
    if expected:
        assert detail['message'] == expected
    # Never echo what was typed, in whole or in part.
    rendered = json.dumps(detail)
    for value in values.values():
        assert value not in rendered and value.strip() not in rendered
    assert 'private' not in rendered
    host.assert_not_called()


@pytest.mark.parametrize('db_password', [HEX_KEY, HEX_KEY.upper()])
def test_conforming_values_and_unconstrained_settings_reach_the_host(monkeypatch, db_password):
    # Upper-case hex is accepted: the recipes' own checks accept either case.
    values = {'DEMO_DB_PASSWORD': db_password, 'DEMO_ADMIN_PASSWORD': 'twelve-chars', 'DEMO_PASSWORD': 'x'}
    host = Mock(return_value={'service_id': 'demo', 'status': 'saved', 'saved_keys': sorted(values)})
    monkeypatch.setattr(extensions, 'request_agent_json', host)
    response = asyncio.run(extensions.extension_configure('demo', request({'values': values}), 'test'))
    assert json.loads(response.body)['saved_keys'] == sorted(values)
    host.assert_called_once()


def test_a_lone_surrogate_is_a_bad_request_not_a_server_error(monkeypatch):
    host = Mock()
    monkeypatch.setattr(extensions, 'request_agent_json', host)
    # Valid JSON ("\ud800" escape) that no UTF-8 .env can hold.
    raw = b'{"values": {"DEMO_ADMIN_PASSWORD": "abcdefghijkl\\ud800"}}'
    with pytest.raises(HTTPException) as error:
        asyncio.run(extensions.extension_configure('demo', request(raw), 'test'))
    assert error.value.status_code == 400
    host.assert_not_called()


def test_undeclared_keys_are_left_to_the_host_agent_to_refuse(monkeypatch):
    host = Mock(side_effect=AgentHTTPError(400, 'private', 'private'))
    monkeypatch.setattr(extensions, 'request_agent_json', host)
    with pytest.raises(HTTPException) as error:
        asyncio.run(extensions.extension_configure('demo', request({'values': {'OTHER_KEY': 'private'}}), 'test'))
    assert error.value.status_code == 400
    host.assert_called_once()


@pytest.mark.parametrize('definition', [None, {'id': 'demo', 'env_vars': [{'key': 'DEMO_KEY', 'format': 'hex65'}]},
                                        {'id': 'demo', 'env_vars': [{'key': 'DEMO_KEY', 'pattern': '^[0-9]+$'}]}])
def test_unreadable_declarations_refuse_without_reaching_the_host(monkeypatch, definition):
    host = Mock()
    monkeypatch.setattr(extensions, 'request_agent_json', host)
    if definition is None:
        monkeypatch.setattr(extensions, '_installation_plan_service',
                            lambda service_id: (_ for _ in ()).throw(ValueError('missing')))
    else:
        monkeypatch.setattr(extensions, '_installation_plan_service', lambda service_id: definition)
    with pytest.raises(HTTPException) as error:
        asyncio.run(extensions.extension_configure('demo', request({'values': {'DEMO_KEY': 'private'}}), 'test'))
    assert error.value.status_code == 400
    assert 'private' not in str(error.value.detail)
    host.assert_not_called()


def test_settings_that_must_differ_are_compared_with_submitted_and_saved_values(monkeypatch):
    host = Mock()
    monkeypatch.setattr(extensions, 'request_agent_json', host)
    for values in ({'DEMO_DB_PASSWORD': HEX_KEY, 'DEMO_API_KEY': HEX_KEY}, {'DEMO_API_KEY': HEX_KEY}):
        SAVED['DEMO_DB_PASSWORD'] = HEX_KEY
        with pytest.raises(HTTPException) as error:
            asyncio.run(extensions.extension_configure('demo', request({'values': values}), 'test'))
        assert error.value.status_code == 422
        assert error.value.detail['message'] == 'DEMO_API_KEY must differ from DEMO_DB_PASSWORD. Nothing was saved.'
        assert HEX_KEY not in json.dumps(error.value.detail)
    host.assert_not_called()
