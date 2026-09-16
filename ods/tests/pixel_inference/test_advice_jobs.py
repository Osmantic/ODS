import asyncio
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time
import uuid

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'bin'))
from pixel_provider.advice_jobs import AdvisoryJobs
from pixel_provider.store import StoreError
from test_advice import request

pytestmark = pytest.mark.skipif(os.name != 'posix',reason='POSIX private jobs')


class FakeCall:
    calls = 0
    def __init__(self,directory,body):
        self.body=body
    def metadata(self):
        return dict(jobId=self.body['requestId'],revision=1,providerId='backup')
    def run(self,*,cancelled,lock_fds):
        assert len(lock_fds)==2
        return asyncio.run(self.execute(cancelled=cancelled))
    async def execute(self,*,cancelled):
        type(self).calls+=1
        for _ in range(50):
            if cancelled():
                raise asyncio.CancelledError()
            await asyncio.sleep(.02)
        return dict(text='Fictional advice',trusted=False)


def wait(manager,job_id):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        value=manager.status(job_id)
        if value['status'] not in ('running','cancelling'):
            return value
        time.sleep(.02)
    pytest.fail('job did not terminate')


@pytest.fixture
def manager(tmp_path):
    root=tmp_path/'providers'; root.mkdir(mode=0o700)
    FakeCall.calls=0
    return AdvisoryJobs(root,call_factory=FakeCall)


def test_idempotent_start_and_conflict_and_no_capsule_on_disk(manager):
    body=request()
    manager.start(body); manager.start(body)
    with pytest.raises(StoreError,match='advice-request-conflict'):
        manager.start(dict(body,capsule='different'))
    result=wait(manager,body['requestId'])
    assert result['status']=='completed' and FakeCall.calls==1
    assert manager.start(body)==result
    claim=(manager.root/body['requestId']/'claim.json').read_text()
    assert body['capsule'] not in claim
    assert (manager.root/body['requestId']).stat().st_mode & 0o777 == 0o700
    assert (manager.root/body['requestId']/'result.json').stat().st_mode & 0o777 == 0o600


def test_two_global_slots_across_independent_managers_and_cancel(manager):
    other=AdvisoryJobs(manager.providers,call_factory=FakeCall)
    a,b,c=request(),request(),request()
    manager.start(a); other.start(b)
    with pytest.raises(StoreError,match='advice-busy'):
        AdvisoryJobs(manager.providers,call_factory=FakeCall).start(c)
    other.cancel(a['requestId'])
    assert wait(manager,a['requestId'])['status']=='cancelled'
    manager.start(c)
    for body in (b,c):
        manager.cancel(body['requestId'])
        assert wait(manager,body['requestId'])['status']=='cancelled'


def test_status_does_not_leak_descriptors(manager):
    body=request(); manager.start(body)
    initial=len(os.listdir('/proc/self/fd'))
    for _ in range(100):
        manager.status(body['requestId'])
    assert len(os.listdir('/proc/self/fd')) <= initial
    manager.cancel(body['requestId']); wait(manager,body['requestId'])


def child_job(root,body,ready):
    jobs=AdvisoryJobs(root,call_factory=FakeCall)
    jobs.start(body); ready.set()
    time.sleep(30)


def test_process_loss_is_interrupted_never_automatic_replay(manager):
    context=multiprocessing.get_context('fork')
    ready=context.Event(); body=request()
    child=context.Process(target=child_job,args=(manager.providers,body,ready))
    child.start()
    try:
        assert ready.wait(3)
        assert manager.status(body['requestId'])['status']=='running'
    finally:
        child.terminate(); child.join(3)
    assert not child.is_alive()
    assert manager.status(body['requestId'])['status']=='interrupted'
    assert manager.start(body)['status']=='interrupted'
    assert FakeCall.calls==0


def test_symlink_job_and_public_directory_fail_closed(manager,tmp_path):
    manager.root.mkdir(mode=0o700)
    body=request(); target=tmp_path/'outside'; target.mkdir(mode=0o700)
    (manager.root/body['requestId']).symlink_to(target,target_is_directory=True)
    with pytest.raises(StoreError):
        manager.start(body)
    assert list(target.iterdir())==[]
    manager.root.chmod(0o755)
    with pytest.raises(StoreError):
        manager.start(request())
