import asyncio
import copy
import os
from pathlib import Path
import sys
import threading
import time
import uuid

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'bin'))
from pixel_provider import advice_setup as setup
from pixel_provider.advice_runtime import RuntimeStore
from pixel_provider.store import StoreError

pytestmark=pytest.mark.skipif(os.name!='posix',reason='POSIX setup jobs')


def body(**changes):
    return dict(dict(requestId=str(uuid.uuid4()),expectedRevision=0,sourceSha256='a'*64,
                     candidateId='b'*64,confirmed=True),**changes)


def wait(jobs,job_id):
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        result=jobs.status(job_id)
        if result['status'] not in ('running','cancelling'): return result
        time.sleep(.02)
    pytest.fail('setup job did not terminate')


@pytest.fixture
def harness(tmp_path,monkeypatch):
    root=tmp_path/'providers'; root.mkdir(mode=0o700)
    entered=threading.Event(); release=threading.Event(); calls=[]
    control={'publish':True,'cancelAfterPublish':False,'lie':False}
    monkeypatch.setattr(setup,'source_digest',lambda:'a'*64)
    def candidate(identity):
        if identity!='b'*64: raise StoreError('advice-python-unavailable')
        return dict(id=identity,path='/fixture/python',version='3.12.3',canPrepare=True)
    monkeypatch.setattr(setup,'select_candidate',candidate)
    def status(directory):
        receipt=RuntimeStore(directory).load()
        return dict(status='ready' if receipt['runtime'] else 'missing',revision=receipt['revision'],
                    runtimeId=receipt['runtime']['id'] if receipt['runtime'] else None)
    monkeypatch.setattr(setup,'runtime_status',status)
    def runner(command,value,*,cancelled,deadline_seconds,lock_fds):
        calls.append(copy.deepcopy(value)); entered.set()
        assert len(lock_fds)==2 and value['lockFds']==list(lock_fds)
        while not release.wait(.01):
            if cancelled(): raise asyncio.CancelledError()
        if control['publish']:
            store=RuntimeStore(root); receipt=store.load()
            store.save(dict(revision=receipt['revision'],schemaVersion=1,runtime=dict(
                id='runtime-'+value['requestId'].replace('-',''),sourceSha256='a'*64,treeSha256='c'*64,
                interpreter='/fixture/python',interpreterSha256='d'*64)),expected_revision=receipt['revision'])
        if control['cancelAfterPublish']: raise asyncio.CancelledError()
        if not control['publish'] and not control['lie']: raise StoreError('setup-failed')
        return dict(schemaVersion=1,requestId=value['requestId'],result=status(root))
    return setup.SetupJobs(root,runner=runner),entered,release,calls,control


def test_durable_idempotent_setup_and_conflicting_uuid(harness):
    jobs,entered,release,calls,_=harness; request=body()
    jobs.start(request); assert entered.wait(2)
    assert jobs.start(request)['status']=='running'
    with pytest.raises(StoreError,match='conflict'):
        jobs.start(dict(request,expectedRevision=1))
    assert jobs.latest()['jobId']==request['requestId']
    release.set(); result=wait(jobs,request['requestId'])
    assert result['status']=='completed' and len(calls)==1
    assert jobs.start(request)==result
    assert not (jobs.providers/'provider-config.json').exists()


def test_global_single_installer_slot_and_cancellation(harness):
    jobs,entered,release,calls,_=harness; request=body()
    try:
        jobs.start(request); assert entered.wait(2)
        other=setup.SetupJobs(jobs.providers,runner=jobs.runner)
        with pytest.raises(StoreError,match='setup-busy'): other.start(body())
        other.cancel(request['requestId'])
        assert wait(jobs,request['requestId'])['status']=='cancelled'
        assert RuntimeStore(jobs.providers).load()['runtime'] is None
    finally: release.set()


def test_late_cancel_reports_actual_publication(harness):
    jobs,_,release,_,control=harness; control['cancelAfterPublish']=True
    request=body(); jobs.start(request); release.set()
    assert wait(jobs,request['requestId'])['status']=='completed'
    assert jobs.cancel(request['requestId'])['status']=='completed'


def test_child_reply_without_pointer_never_means_installed(harness):
    jobs,_,release,_,control=harness; control.update(publish=False,lie=True)
    request=body(); jobs.start(request); release.set()
    assert wait(jobs,request['requestId'])['status']=='failed'


def test_lost_terminal_receipt_reconciles_without_replay(harness):
    jobs,_,release,calls,_=harness; request=body(); jobs.start(request); release.set()
    assert wait(jobs,request['requestId'])['status']=='completed'
    jobs.threads[request['requestId']].join(2)
    (jobs.root/request['requestId']/'result.json').unlink()
    assert jobs.status(request['requestId'])['status']=='completed'
    assert jobs.start(request)['status']=='completed' and len(calls)==1


def test_interrupted_without_pointer_is_not_replayed(harness):
    jobs,_,release,calls,control=harness; control['publish']=False
    request=body(); jobs.start(request); release.set(); wait(jobs,request['requestId'])
    jobs.threads[request['requestId']].join(2)
    (jobs.root/request['requestId']/'result.json').unlink()
    assert jobs.start(request)['status']=='interrupted' and len(calls)==1


@pytest.mark.parametrize('change',[dict(confirmed=False),dict(confirmed=1),dict(python='/tmp/evil'),
    dict(command=['evil']),dict(expectedRevision=True),dict(sourceSha256='bad'),dict(candidateId='bad')])
def test_no_arbitrary_path_and_validation_before_claim(harness,change):
    jobs,_,_,calls,_=harness
    with pytest.raises(StoreError): jobs.start(body(**change))
    assert not calls and not jobs.root.exists()


@pytest.mark.parametrize('change',[dict(expectedRevision=1),dict(sourceSha256='c'*64),dict(candidateId='c'*64)])
def test_stale_source_revision_or_candidate_before_claim(harness,change):
    jobs,_,_,calls,_=harness; request=body(**change)
    with pytest.raises(StoreError): jobs.start(request)
    assert not calls and not (jobs.root/request['requestId']).exists()


def test_unconfigured_readiness_does_not_create_storage(tmp_path):
    root=tmp_path/'absent'
    result=setup.readiness(root)
    assert result['status']=='not-configured' and result['candidates']==[] and not root.exists()
