import hashlib
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'bin'))
from pixel_provider.handoff_approvals import HandoffApprovals,CHECKPOINT_LIMIT
from pixel_provider.store import StoreError
from test_handoff_host import checkpoint

pytestmark=pytest.mark.skipif(os.name!='posix',reason='POSIX handoff custody')
WORKER=Path(__file__).resolve().parents[2]/'bin/pixel_provider/handoff_worker.py'


@pytest.fixture
def manager(tmp_path):
    providers=tmp_path/'providers'; providers.mkdir(mode=0o700)
    return HandoffApprovals(providers)


def encode(value):
    raw=json.dumps(value,ensure_ascii=False)
    return raw,hashlib.sha256(raw.encode()).hexdigest()


def decide(manager,value,digest,approved=True,cloud=False,cost=False):
    return manager.decide(dict(runId=value['runId'],checkpointDigest=digest,approved=approved,
        allowCloud=cloud,acceptUnknownCost=cost))


def test_large_exact_preview_and_custody(manager):
    value,_,_=checkpoint(); value['prompt']='Private unicode ★ '*30000
    raw,digest=encode(value)
    assert 256*1024<len(raw.encode())<CHECKPOINT_LIMIT
    with manager.publish(raw,digest,60) as pending:
        assert pending.receipt() is None
        result=manager.status(value['runId'],checkpoint=True)
        assert result['checkpointJson']==raw and result['checkpointBytes']==len(raw.encode())
        assert 'checkpointJson' not in manager.pending()['items'][0]
        for path in manager.root.rglob('*'):
            assert path.stat().st_mode&0o777==(0o700 if path.is_dir() else 0o600)
        decide(manager,value,digest)
        assert pending.receipt()==dict(approved=True,checkpointDigest=digest)
        pending.finish('approved')
    assert manager.status(value['runId'])['status']=='approved'


@pytest.mark.parametrize('cloud,cost',[(False,False),(True,False),(False,True),(True,True)])
def test_cloud_requires_both_consents(manager,cloud,cost):
    value,_,_=checkpoint(); value['recipient'].update(kind='cloud',baseUrl='https://api.example/v1')
    raw,digest=encode(value)
    with manager.publish(raw,digest,60) as pending:
        if cloud and cost:
            decide(manager,value,digest,cloud=cloud,cost=cost)
            assert pending.receipt()==dict(approved=True,checkpointDigest=digest,allowCloud=True,acceptUnknownCost=True)
        else:
            with pytest.raises(StoreError): decide(manager,value,digest,cloud=cloud,cost=cost)
            assert pending.receipt() is None


def test_expiry_and_immutable_replay_claim(manager):
    clock=[1000000]; manager.clock=lambda:clock[0]
    value,raw,digest=checkpoint()
    with manager.publish(raw,digest,1) as pending:
        clock[0]+=2
        assert manager.status(value['runId'])['status']=='expired'
        with pytest.raises(StoreError): decide(manager,value,digest)
        assert pending.receipt() is None
        pending.finish('expired')
    with pytest.raises(StoreError,match='handoff-run-replayed'):
        with manager.publish(raw,digest,60): pass


def test_identical_decision_idempotent_and_conflicting_decision_rejected(manager):
    value,raw,digest=checkpoint()
    with manager.publish(raw,digest,60) as pending:
        first=decide(manager,value,digest)
        assert decide(manager,value,digest)==first
        with pytest.raises(StoreError,match='conflict'): decide(manager,value,digest,approved=False)
        with pytest.raises(StoreError): pending.finish('declined')
        pending.finish('approved')


def test_final_approval_requires_real_matching_decision(manager):
    value,raw,digest=checkpoint()
    with manager.publish(raw,digest,60) as pending:
        with pytest.raises(StoreError): pending.finish('approved')
        path=manager.root/value['runId']/'result.json'
        path.write_text('{"status":"approved"}'); path.chmod(0o600)
        with pytest.raises(StoreError): manager.status(value['runId'])


def test_corrupt_checkpoint_blocks_owner_and_worker(manager):
    value,raw,digest=checkpoint()
    with manager.publish(raw,digest,60) as pending:
        decide(manager,value,digest)
        (manager.root/value['runId']/'checkpoint.json').write_bytes(b'{}')
        with pytest.raises(StoreError): decide(manager,value,digest)
        with pytest.raises(StoreError): pending.receipt()


def test_polling_retained_terminal_claims_does_not_read_transcripts(manager,monkeypatch):
    value,raw,digest=checkpoint()
    with manager.publish(raw,digest,60) as pending: pending.finish('cancelled')
    def forbidden(*_args): raise AssertionError('terminal transcript read')
    monkeypatch.setattr(manager,'_read',forbidden)
    assert manager.pending()==dict(items=[],unavailableCount=0)


def test_partial_abandoned_publication_does_not_hide_valid_pending_run(manager):
    value,raw,digest=checkpoint()
    with manager.publish(raw,digest,60):
        partial=manager.root/str(uuid.uuid4()); partial.mkdir(mode=0o700)
        result=manager.pending()
        assert result['unavailableCount']==1
        assert [row['runId'] for row in result['items']]==[value['runId']]
        assert list(partial.iterdir())==[partial/'.provider-config.lock']


def test_concurrent_conflicting_owner_decisions_have_one_immutable_winner(manager):
    value,raw,digest=checkpoint(); barrier=threading.Barrier(2)
    with manager.publish(raw,digest,60) as pending:
        def attempt(approved):
            barrier.wait(timeout=3)
            try: return decide(manager,value,digest,approved=approved)['status']
            except StoreError as exc: return exc.code
        with ThreadPoolExecutor(max_workers=2) as workers:
            results=list(workers.map(attempt,[True,False]))
        assert results.count('handoff-decision-conflict')==1
        receipt=pending.receipt()
        expected='approved' if receipt['approved'] else 'declined'
        assert expected in results
        with pytest.raises(StoreError,match='conflict'):
            decide(manager,value,digest,approved=not receipt['approved'])
        pending.finish(expected)
    assert manager.status(value['runId'])['status']==expected


@pytest.mark.parametrize('change',[{'messages':[{'role':'toolResult','toolCallId':'orphan'}]},
    {'messages':[{'role':'assistant','content':[{'type':'toolCall','id':'missing'}]}]},
    {'prompt':'x'*CHECKPOINT_LIMIT}])
def test_incomplete_history_and_oversize_rejected(manager,change):
    value,_,_=checkpoint(); value.update(change); raw,digest=encode(value)
    with pytest.raises(StoreError): manager.publish(raw,digest,60)


@pytest.mark.parametrize('timeout',[True,False,0,121,1.5])
def test_timeout_strict_integer(manager,timeout):
    _,raw,digest=checkpoint()
    with pytest.raises(StoreError): manager.publish(raw,digest,timeout)


def start_worker(manager,timeout=3):
    value,raw,digest=checkpoint()
    body=json.dumps(dict(schemaVersion=1,checkpointJson=raw,checkpointDigest=digest,timeoutSeconds=timeout)).encode()
    process=subprocess.Popen([sys.executable,'-I','-B',str(WORKER),'--provider-directory',str(manager.providers)],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    process.stdin.write(struct.pack('!I',len(body))+body); process.stdin.flush()
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        if (manager.root/value['runId']/'claim.json').exists(): return process,value,digest
        assert process.poll() is None
        time.sleep(.02)
    process.kill(); process.wait(); pytest.fail('publication did not appear')


@pytest.mark.parametrize('approved',[True,False])
def test_actual_private_worker_returns_exact_receipt_and_exits(manager,approved):
    process,value,digest=start_worker(manager)
    try:
        decide(manager,value,digest,approved=approved)
        assert select.select([process.stdout],[],[],4)[0]
        size=struct.unpack('!I',process.stdout.read(4))[0]
        assert json.loads(process.stdout.read(size))==dict(approved=approved,checkpointDigest=digest)
        assert process.wait(timeout=3)==0
        assert process.stdout.read()==b''
        assert manager.status(value['runId'])['status']==('approved' if approved else 'declined')
    finally:
        if process.poll() is None: process.kill(); process.wait()
        for stream in (process.stdin,process.stdout,process.stderr): stream.close()


@pytest.mark.parametrize('mode',['eof','sigkill','extra-frame','deadline'])
def test_actual_worker_loss_and_deadline_never_authorize(manager,mode):
    process,value,digest=start_worker(manager,1 if mode=='deadline' else 3)
    try:
        if mode=='eof': process.stdin.close()
        elif mode=='extra-frame': process.stdin.write(b'x'); process.stdin.flush()
        elif mode=='sigkill': process.kill()
        assert process.wait(timeout=5)!=0
        assert process.stdout.read()==b''
        assert manager.status(value['runId'])['status'] in ('interrupted','cancelled','expired')
        with pytest.raises(StoreError): decide(manager,value,digest)
        with pytest.raises(StoreError):
            raw,_=encode(dict(checkpoint()[0],runId=value['runId']))
            with manager.publish(raw,hashlib.sha256(raw.encode()).hexdigest(),60): pass
    finally:
        if process.poll() is None: process.kill(); process.wait()
        for stream in (process.stdin,process.stdout,process.stderr): stream.close()
