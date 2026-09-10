"""Owner sharing controls through a disposable, actual host-agent HTTP server."""

import http.client
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_pixel_provider_host_api as host_fixture

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX private state')


@pytest.fixture
def owner(tmp_path):
    host_fixture.HostHTTP.setUpClass()
    agent = host_fixture.HostHTTP.agent
    agent.DATA_DIR = tmp_path
    route = {'catalogId':'glm','runtimeModelId':'GLM','routeSeq':4,'contextLength':32768,
             'capabilities':{'chat':True,'tools':True,'vision':False,'agentViable':True}}
    # This fixture qualifies actual HTTP/auth/private persistence, not the
    # runner's Docker daemon. A live inspect can outlast the client deadline
    # and accidentally probe an unrelated install. Lifecycle tests replace
    # this stopped service with their explicit, independently controlled one.
    stopped = SimpleNamespace(port=4005, status=lambda: {'status': 'stopped'})
    with patch.object(agent, '_pixel_share_active_route', return_value=route), \
         patch.object(agent, '_pixel_sharing_service', return_value=stopped), \
         patch.object(agent, '_read_progress_status', return_value=None):
        yield agent, host_fixture.HostHTTP.server, route
    host_fixture.HostHTTP.tearDownClass()


def request(owner, action=None, body=None, token='synthetic-provider-test-key'):
    _, server, _ = owner
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    try:
        path = '/v1/pixel/inference-sharing' + ('/' + action if action else '')
        connection.request('POST' if action else 'GET', path,
            body=json.dumps(body) if body is not None else None,
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        response = connection.getresponse()
        return response.status, json.loads(response.read()), response.getheader('Cache-Control')
    finally:
        connection.close()


def settings():
    return dict(label='Laptop',catalogId='glm',runtimeModelId='GLM',ttlSeconds=3600,
                maxConcurrent=1,maxOutputTokens=64,deadlineSeconds=60,requestsPerMinute=20)


def test_owner_issue_enable_revoke_cas_and_no_secret_readback(owner):
    status, initial, cache = request(owner)
    assert status == 200 and initial['configuration']['revision'] == 0 and cache == 'no-store'
    assert not list(owner[0].DATA_DIR.iterdir())
    status, issued, cache = request(owner,'issue',{'expectedRevision':0,'settings':settings()})
    assert status == 200 and cache == 'no-store'
    key = issued['credential']['key']
    assert key not in json.dumps(request(owner)) and 'tokenHash' not in json.dumps(request(owner))
    from pixel_provider.sharing import SharingStore
    from pixel_provider.store import StoreError
    store = SharingStore(owner[0].DATA_DIR / 'pixel-inference')
    with pytest.raises(StoreError, match='invalid-credential'):
        store.authenticate(key)
    assert request(owner,'enable',{'expectedRevision':1,'enabled':True})[0] == 200
    assert store.authenticate(key)['label'] == 'Laptop'
    assert request(owner,'enable',{'expectedRevision':1,'enabled':False})[0] == 409
    assert request(owner,'revoke',{'expectedRevision':2,'deviceId':issued['credential']['id']})[0] == 200
    with pytest.raises(StoreError, match='invalid-credential'):
        store.authenticate(key)


def test_owner_auth_and_validation_before_persistence(owner):
    assert request(owner,token='bad')[0] == 403
    assert request(owner,'issue',{},token='bad')[0] == 403
    for body in ({}, {'expectedRevision':True,'settings':settings()},
                 {'expectedRevision':0,'settings':{**settings(),'maxConcurrent':999}},
                 {'expectedRevision':0,'settings':settings(),'extra':True}):
        assert request(owner,'issue',body)[0] == 400
    assert request(owner,'issue',{'expectedRevision':0,'settings':{**settings(),'catalogId':'other'}})[0] == 409
    assert not list(owner[0].DATA_DIR.iterdir())


def test_disable_and_revoke_do_not_need_active_route(owner):
    issued = request(owner,'issue',{'expectedRevision':0,'settings':settings()})[1]
    with patch.object(owner[0], '_pixel_share_active_route', return_value=None):
        assert request(owner,'enable',{'expectedRevision':1,'enabled':True})[0] == 409
        assert request(owner,'enable',{'expectedRevision':1,'enabled':False})[0] == 200
        assert request(owner,'revoke',{'expectedRevision':2,'deviceId':issued['credential']['id']})[0] == 200


def test_corrupt_state_is_unavailable_not_empty(owner):
    request(owner,'issue',{'expectedRevision':0,'settings':settings()})
    path = owner[0].DATA_DIR / 'pixel-inference/inference-sharing.json'
    path.write_text('{"secret":"do-not-echo"}')
    status, result, _ = request(owner)
    assert status == 503 and 'do-not-echo' not in json.dumps(result)


@pytest.fixture
def lifecycle(owner):
    started, release = threading.Event(), threading.Event()
    progress = []
    class Service:
        port = 4015
        fail = False
        failure = None
        state = 'stopped'
        def start(self):
            started.set()
            assert release.wait(3), 'test did not release lifecycle'
            if self.failure is not None:
                raise self.failure
            if self.fail:
                raise RuntimeError('private-lifecycle-sentinel')
            self.state = 'ready'
        def stop(self):
            started.set()
            assert release.wait(3), 'test did not release lifecycle'
            self.state = 'stopped'
        def status(self):
            return {'status': self.state}
    service = Service()
    agent = owner[0]
    with patch.object(agent, '_pixel_sharing_service', return_value=service), \
         patch.object(agent, '_write_progress', side_effect=lambda *args, **kwargs: progress.append((args,kwargs))), \
         patch.object(agent, '_read_progress_status', side_effect=lambda _: progress[-1][0][1] if progress else None):
        yield service, started, release, progress
        release.set()
        deadline = time.monotonic() + 4
        while agent._service_locks['pixel-inference'].locked() and time.monotonic() < deadline:
            time.sleep(.01)
        assert not agent._service_locks['pixel-inference'].locked()


def test_start_requires_live_grant_then_returns_202_and_serializes_lifecycle(owner, lifecycle):
    service, started, release, progress = lifecycle
    assert request(owner, 'start', {'expectedRevision':0})[0] == 400
    assert not started.is_set()
    request(owner, 'issue', {'expectedRevision':0,'settings':settings()})
    status, value, cache = request(owner, 'start', {'expectedRevision':1})
    assert status == 202 and cache == 'no-store'
    assert value['configuration']['revision'] == 2 and value['configuration']['enabled']
    assert value['runtime']['status'] == 'starting' and value['transport']['port'] == 4015
    assert started.wait(1)
    assert request(owner, 'stop', {'expectedRevision':2})[0] == 409
    release.set()
    deadline = time.monotonic() + 2
    while owner[0]._service_locks['pixel-inference'].locked() and time.monotonic() < deadline:
        time.sleep(.01)
    assert request(owner)[1]['runtime']['status'] == 'ready'
    assert progress[-1][0][1] == 'complete'
    assert request(owner, 'stop', {'expectedRevision':2})[0] == 202


def test_failed_start_disables_admission_and_does_not_expose_error(owner, lifecycle):
    service, started, release, progress = lifecycle
    service.fail = True
    request(owner, 'issue', {'expectedRevision':0,'settings':settings()})
    assert request(owner, 'start', {'expectedRevision':1})[0] == 202
    assert started.wait(1)
    release.set()
    deadline = time.monotonic() + 2
    while owner[0]._service_locks['pixel-inference'].locked() and time.monotonic() < deadline:
        time.sleep(.01)
    status, value, _ = request(owner)
    assert status == 200 and value['runtime']['status'] == 'error'
    assert not value['configuration']['enabled'] and value['configuration']['revision'] == 3
    assert 'private-lifecycle-sentinel' not in json.dumps(progress)


def test_failed_start_reports_safe_template_reason_without_changing_response_schema(owner, lifecycle, caplog):
    from pixel_provider.store import StoreError
    service, started, release, progress = lifecycle
    service.failure = StoreError('unsafe-sharing-compose')
    request(owner, 'issue', {'expectedRevision': 0, 'settings': settings()})
    assert request(owner, 'start', {'expectedRevision': 1})[0] == 202
    assert started.wait(1)
    release.set()
    deadline = time.monotonic() + 2
    while owner[0]._service_locks['pixel-inference'].locked() and time.monotonic() < deadline:
        time.sleep(.01)
    status, value, _ = request(owner)
    assert status == 200 and value['runtime'] == {'status': 'error'}
    assert not value['configuration']['enabled'] and value['configuration']['revision'] == 3
    assert 'unsafe-sharing-compose' in progress[-1][1]['error']
    assert 'Inference sharing start failed: unsafe-sharing-compose' in caplog.text


@pytest.mark.parametrize('action', ['start','stop'])
def test_lifecycle_rejects_unknown_fields_or_bool_revision_before_start(owner, lifecycle, action):
    for body in ({'expectedRevision':True}, {'expectedRevision':0,'command':'anything'}):
        assert request(owner, action, body)[0] == 400
    assert not lifecycle[1].is_set()


def test_failed_start_preserves_concurrent_revocation_but_closes_admission(owner, lifecycle):
    service, started, release, _ = lifecycle
    service.fail = True
    issued = request(owner, 'issue', {'expectedRevision':0,'settings':settings()})[1]
    assert request(owner, 'start', {'expectedRevision':1})[0] == 202
    assert started.wait(1)
    assert request(owner, 'enable', {'expectedRevision':2,'enabled':True})[0] == 409
    assert request(owner, 'revoke', {'expectedRevision':2,'deviceId':issued['credential']['id']})[0] == 200
    release.set()
    deadline = time.monotonic() + 2
    while owner[0]._service_locks['pixel-inference'].locked() and time.monotonic() < deadline:
        time.sleep(.01)
    value = request(owner)[1]['configuration']
    assert value['revision'] == 4 and not value['enabled'] and value['devices'][0]['revoked']


def test_progress_write_failure_closes_admission_before_thread_launch(owner, lifecycle):
    from pixel_provider.sharing import SharingStore
    request(owner, 'issue', {'expectedRevision':0,'settings':settings()})
    with patch.object(owner[0], '_write_progress', side_effect=OSError('private-progress-error')):
        assert request(owner, 'start', {'expectedRevision':1})[0] == 503
    assert not lifecycle[1].is_set()
    assert not owner[0]._service_locks['pixel-inference'].locked()
    assert not SharingStore(owner[0].DATA_DIR/'pixel-inference').load()['enabled']
