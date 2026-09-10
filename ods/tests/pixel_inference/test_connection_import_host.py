"""Actual disposable owner HTTP -> child -> peer metadata HTTP, never a live model."""
import http.client
import json
import os
import sys
from pathlib import Path

import pytest
from test_connection_transport import server as peer_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_pixel_provider_host_api as host_fixture

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='installed host API is Linux-qualified')


@pytest.fixture
def owner(tmp_path):
    host_fixture.HostHTTP.setUpClass()
    host_fixture.HostHTTP.agent.DATA_DIR = tmp_path
    try:
        yield host_fixture.HostHTTP.server, tmp_path
    finally:
        host_fixture.HostHTTP.tearDownClass()


def request(owner, body, token='synthetic-provider-test-key'):
    client = http.client.HTTPConnection(*owner[0].server_address, timeout=5)
    try:
        client.request('POST', '/v1/pixel/providers/connection-probe', body=body,
                       headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        response = client.getresponse()
        return response.status, dict(response.headers), json.loads(response.read())
    finally:
        client.close()


def test_actual_owner_and_metadata_chain_without_saved_settings(owner):
    with peer_server() as (connection, calls):
        body = json.dumps({'bundle': json.dumps(connection), 'confirmedEndpoint': connection['baseUrl']})
        code, headers, value = request(owner, body)
        assert code == 200 and headers['Cache-Control'] == 'no-store'
        assert value['metadata']['routedModel'] == 'GLM'
        assert connection['credential']['apiKey'] not in json.dumps(value)
    assert len(calls) == 1 and calls[0][0] == '/v1/models'
    assert list(owner[1].iterdir()) == []


@pytest.mark.parametrize('body,token,status', [
    ('invalid', 'wrong', 403), ('{}', 'synthetic-provider-test-key', 400),
    ('{"bundle":"{}","bundle":"{}","confirmedEndpoint":"https://example.org/v1"}', 'synthetic-provider-test-key', 400),
    ('x' * 65537, 'synthetic-provider-test-key', 413),
])
def test_denial_and_bad_input_never_spawn_probe(owner, body, token, status, monkeypatch):
    from pixel_provider import connection_import
    def forbidden(*args, **kwargs):
        pytest.fail('invalid request started subprocess')
    monkeypatch.setattr(connection_import.subprocess, 'run', forbidden)
    assert request(owner, body, token)[0] == status
    assert list(owner[1].iterdir()) == []
