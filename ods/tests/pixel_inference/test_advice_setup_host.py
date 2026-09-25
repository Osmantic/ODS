import http.client
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_pixel_provider_host_api as host_fixture

pytestmark=pytest.mark.skipif(os.name!='posix',reason='POSIX host setup')

@pytest.fixture
def host(tmp_path):
    host_fixture.HostHTTP.setUpClass()
    host_fixture.HostHTTP.agent.DATA_DIR=tmp_path
    try: yield host_fixture.HostHTTP, tmp_path
    finally: host_fixture.HostHTTP.tearDownClass()


def request(host,action=None,body=None,token='synthetic-provider-test-key'):
    fixture,_=host
    connection=http.client.HTTPConnection(*fixture.server.server_address,timeout=4)
    try:
        connection.request('POST' if action else 'GET','/v1/pixel/advice-runtime'+('/'+action if action else ''),
            body=json.dumps(body) if body is not None else None,
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        result=connection.getresponse()
        return result.status,json.loads(result.read()),result.getheader('Cache-Control')
    finally: connection.close()


def test_pristine_readiness_passive_and_private(host):
    status,result,cache=request(host)
    assert status==200 and result['status']=='not-configured' and cache=='no-store'
    assert list(host[1].iterdir())==[]


@pytest.mark.parametrize('action',[None,'prepare','status','cancel'])
def test_owner_auth_precedes_state(host,action):
    assert request(host,action,{},token='wrong')[0]==403
    assert list(host[1].iterdir())==[]


@pytest.mark.parametrize('body',[{},dict(python='/tmp/evil'),dict(command=['evil']),
    dict(requestId='../bad'),dict(requestId='bad',confirmed=True)])
def test_no_client_paths_or_unconfirmed_install(host,body):
    assert request(host,'prepare',body)[0]==400
    assert list(host[1].iterdir())==[]
