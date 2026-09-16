import http.client
import json
import os
import uuid

import pytest
from test_handoff_host import host  # shared real stdlib host fixture

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX scope storage')


def request(host, action, body, token='synthetic-provider-test-key', raw=None):
    connection = http.client.HTTPConnection(*host[0].server.server_address, timeout=4)
    try:
        connection.request('POST', '/v1/pixel/provider-scopes/'+action, body=raw or json.dumps(body),
            headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        return response.status, json.loads(response.read()), response.getheader('Cache-Control')
    finally: connection.close()


@pytest.mark.parametrize('action', ['status', 'begin', 'end', 'select', 'return'])
def test_auth_precedes_state(host, action):
    assert request(host, action, {}, token='bad')[0] == 403
    assert not list(host[1].iterdir())


def test_real_begin_readback_end_and_stale_write(host):
    assert request(host, 'status', {'chatId': 'Chat_A'})[1]['taskId'] is None
    assert not list(host[1].iterdir())
    body = dict(chatId='Chat_A', taskId=str(uuid.uuid4()), expectedRevision=0)
    status, started, cache = request(host, 'begin', body)
    assert status == 200 and started['taskId'] == body['taskId'] and cache == 'no-store'
    assert request(host, 'begin', body)[0] == 409
    assert request(host, 'status', {'chatId': 'Chat_A'})[1] == started
    body['expectedRevision'] = 1
    assert request(host, 'end', body)[1]['taskId'] is None


def test_strict_bounded_request_and_no_public_resolver(host):
    assert request(host, 'status', {}, raw='{"chatId":"a","chatId":"b"}')[0] == 400
    assert request(host, 'status', {}, raw=' '*4097)[0] == 413
    assert request(host, 'resolve', {'sessionKey': 'bad'})[0] == 404
