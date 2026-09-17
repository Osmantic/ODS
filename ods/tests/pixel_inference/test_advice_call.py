"""Contract tests for pixel_provider.advice_call.WorkerAdvisoryCall.

The worker-backed advisory call is admitted by advice_jobs.py after the owner
prepares a private runtime. These tests pin the two boundaries that protect a
running advisory job: the frozen runtime receipt is re-resolved at run time (an
explicit reprepare never retargets an accepted call), and the worker answer is
accepted only under the exact advisory response contract (untrusted text,
unknown cost, bounded usage fields).
"""
import os
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider import advice_call as call_module
from pixel_provider.advice_call import WorkerAdvisoryCall
from pixel_provider.config import public_config
from pixel_provider.store import StoreError
from pixel_provider.vault import CredentialStore
from test_provider_runtime import configuration

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX private store')

COMMAND = ['/srv/advice-venv/bin/python', '-I', '-B', '/srv/advice-venv/source/pixel_provider/advice_worker.py']


def request(**changes):
    return dict(dict(requestId=str(uuid.uuid4()), expectedRevision=1, providerId='backup',
        capsule='Review only this fictional plan.', allowCloud=False, acceptUnknownCost=False,
        maxOutputTokens=512, deadlineSeconds=10), **changes)


def answer(request_id, **result_changes):
    result = dict(text='Ship the smaller change first.', trusted=False, costStatus='unknown',
                  usage=dict(prompt_tokens=11, completion_tokens=7, total_tokens=18))
    result.update(result_changes)
    return dict(schemaVersion=1, requestId=request_id, result=result)


@pytest.fixture
def saved(tmp_path):
    root = tmp_path / 'providers'
    root.mkdir(mode=0o700)
    config = configuration()
    config['revision'] = 0
    config['roles']['advisor'] = 'backup'
    CredentialStore(root).save_public(dict(expectedRevision=0, document=public_config(config),
        credentialChanges={'backup': dict(action='set', value='fixture-advisor-key')}))
    return root


@pytest.fixture
def admitted(saved, monkeypatch):
    resolutions = []
    monkeypatch.setattr(call_module, 'resolve_runtime',
                        lambda directory, *, receipt: (resolutions.append(receipt), COMMAND)[1])
    call = WorkerAdvisoryCall(saved, request())
    return call, resolutions


def test_requires_a_prepared_runtime_before_admission(saved):
    with pytest.raises(StoreError, match='advice-runtime-missing'):
        WorkerAdvisoryCall(saved, request())


def test_run_receives_frozen_command_and_full_snapshot(admitted, monkeypatch):
    call, _ = admitted
    deliveries = []
    def fake_worker(command, snapshot, *, cancelled, deadline_seconds, lock_fds):
        deliveries.append((command, snapshot, deadline_seconds, lock_fds))
        return answer(call.body['requestId'])
    monkeypatch.setattr(call_module, 'run_worker', fake_worker)
    cancel_sentinel = lambda: False
    result = call.run(cancelled=cancel_sentinel, lock_fds=(7, 8))
    assert result['text'] == 'Ship the smaller change first.'
    command, snapshot, deadline_seconds, lock_fds = deliveries[0]
    assert command == COMMAND and deadline_seconds == 10 and lock_fds == (7, 8)
    assert set(snapshot) == {'schemaVersion', 'requestId', 'snapshot'}
    assert snapshot['schemaVersion'] == 1 and snapshot['requestId'] == call.body['requestId']
    assert snapshot['snapshot']['body'] == call.body
    assert snapshot['snapshot']['credentials'] == {'backup': 'fixture-advisor-key'}


def test_run_revalidates_the_same_frozen_receipt(admitted):
    call, resolutions = admitted
    assert len(resolutions) == 1 and resolutions[0] is call.receipt


def test_reprepare_during_run_cannot_retarget_the_command(admitted, monkeypatch):
    call, resolutions = admitted
    seen = []
    def fake_worker(command, snapshot, *, cancelled, deadline_seconds, lock_fds):
        seen.append(command)
        return answer(call.body['requestId'])
    monkeypatch.setattr(call_module, 'run_worker', fake_worker)
    call.run(cancelled=lambda: False, lock_fds=())
    assert seen == [COMMAND] and len(resolutions) == 2
    assert resolutions[0] is resolutions[1] is call.receipt


@pytest.mark.parametrize('mutate', [
    lambda a: a.update(schemaVersion=2),
    lambda a: a.update(requestId=str(uuid.uuid4())),
    lambda a: a.pop('requestId'),
    lambda a: a.update(extra='field'),
])
def test_rejects_malformed_worker_envelope(admitted, monkeypatch, mutate):
    call, _ = admitted
    def fake_worker(command, snapshot, *, cancelled, deadline_seconds, lock_fds):
        value = answer(call.body['requestId'])
        mutate(value)
        return value
    monkeypatch.setattr(call_module, 'run_worker', fake_worker)
    with pytest.raises(StoreError, match='invalid-advice-response'):
        call.run(cancelled=lambda: False, lock_fds=())


@pytest.mark.parametrize('result', [
    dict(text='ok', trusted=True, costStatus='unknown',
         usage=dict(prompt_tokens=1, completion_tokens=1, total_tokens=2)),
    dict(text='ok', trusted=False, costStatus='estimated',
         usage=dict(prompt_tokens=1, completion_tokens=1, total_tokens=2)),
    dict(text='   ', trusted=False, costStatus='unknown',
         usage=dict(prompt_tokens=1, completion_tokens=1, total_tokens=2)),
    dict(text='x' * 65537, trusted=False, costStatus='unknown',
         usage=dict(prompt_tokens=1, completion_tokens=1, total_tokens=2)),
    dict(text='ok', trusted=False, costStatus='unknown',
         usage=dict(prompt_tokens=1, completion_tokens=1)),
    dict(text='ok', trusted=False, costStatus='unknown',
         usage=dict(prompt_tokens=-1, completion_tokens=1, total_tokens=0)),
    dict(text='ok', trusted=False, costStatus='unknown',
         usage=dict(prompt_tokens=10**10 + 1, completion_tokens=1, total_tokens=0)),
    dict(text='ok', trusted=False, costStatus='unknown',
         usage=dict(prompt_tokens=1.5, completion_tokens=1, total_tokens=2)),
    dict(text='ok', trusted=False, costStatus='unknown', usage=None),
    dict(text='ok', trusted=False, costStatus='unknown'),
])
def test_rejects_result_outside_advisory_contract(admitted, monkeypatch, result):
    call, _ = admitted
    monkeypatch.setattr(call_module, 'run_worker',
        lambda command, snapshot, *, cancelled, deadline_seconds, lock_fds:
            dict(schemaVersion=1, requestId=call.body['requestId'], result=result))
    with pytest.raises(StoreError, match='invalid-advice-response'):
        call.run(cancelled=lambda: False, lock_fds=())


def test_accepts_unmeasured_usage_fields(admitted, monkeypatch):
    call, _ = admitted
    monkeypatch.setattr(call_module, 'run_worker',
        lambda command, snapshot, *, cancelled, deadline_seconds, lock_fds:
            answer(call.body['requestId'],
                   usage=dict(prompt_tokens=None, completion_tokens=None, total_tokens=None)))
    result = call.run(cancelled=lambda: False, lock_fds=())
    assert result['usage'] == dict(prompt_tokens=None, completion_tokens=None, total_tokens=None)


def test_worker_transport_failure_propagates(admitted, monkeypatch):
    call, _ = admitted
    def broken(*args, **kwargs):
        raise StoreError('advice-worker-failed')
    monkeypatch.setattr(call_module, 'run_worker', broken)
    with pytest.raises(StoreError, match='advice-worker-failed'):
        call.run(cancelled=lambda: False, lock_fds=())
