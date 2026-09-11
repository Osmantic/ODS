"""Actual optional worker, upstream TCP and parent SIGKILL, not mocked jobs."""
import asyncio
import copy
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import multiprocessing
import os
from pathlib import Path
import select
import socket
import sys
import threading
import time

import pytest

from test_advice import request,saved
from pixel_provider.advice import AdvisoryCall
from pixel_provider.advice_process import run_worker
from pixel_provider.store import StoreError
from pixel_provider.vault import CredentialStore

pytestmark=pytest.mark.skipif(os.name != 'posix',reason='POSIX advisory worker')
SCRIPT=Path(__file__).resolve().parents[2]/'bin'/'pixel_provider'/'advice_worker.py'


@pytest.fixture
def upstream(saved):
    entered=threading.Event(); disconnected=threading.Event(); requests=[]
    options={'hold':False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            entered.set()
            if options['hold']:
                if select.select([self.connection],[],[],5)[0] and self.connection.recv(1,socket.MSG_PEEK)==b'':
                    disconnected.set()
            else:
                payload=json.dumps(dict(model='backup',choices=[dict(message=dict(role='assistant',content='Use a small canary.'))])).encode()
                self.send_response(200); self.send_header('Content-Length',str(len(payload))); self.end_headers()
                self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    root,config=saved
    config['providers'][1]['baseUrl']=f'http://127.0.0.1:{server.server_port}/v1'
    CredentialStore(root).save_public(dict(expectedRevision=1,document=config,
        credentialChanges={'backup':dict(action='set',value='fixture-advisor-key')}))
    call=AdvisoryCall(root,request(expectedRevision=2))
    try:
        yield call,options,entered,disconnected,requests
    finally:
        server.shutdown(); server.server_close(); thread.join(3)


def payload(call):
    return dict(schemaVersion=1,requestId=call.body['requestId'],snapshot=call.snapshot())


def test_actual_worker_completion(upstream):
    call,_,entered,_,requests=upstream
    result=run_worker([sys.executable,'-I','-B',str(SCRIPT)],payload(call),cancelled=lambda:False,deadline_seconds=5)
    assert result['requestId']==call.body['requestId']
    assert result['result']['text']=='Use a small canary.'
    assert result['result']['trusted'] is False
    assert entered.is_set() and len(requests)==1 and 'tools' not in requests[0]


def test_actual_worker_cancellation_closes_model_socket(upstream):
    call,options,entered,disconnected,requests=upstream; options['hold']=True
    with pytest.raises(asyncio.CancelledError):
        run_worker([sys.executable,'-I','-B',str(SCRIPT)],payload(call),cancelled=entered.is_set,deadline_seconds=5)
    assert disconnected.wait(3) and len(requests)==1


def orphan_driver(value,lock):
    import fcntl
    fd=os.open(lock,os.O_RDWR|os.O_CREAT,0o600)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        run_worker([sys.executable,'-I','-B',str(SCRIPT)],value,cancelled=lambda:False,deadline_seconds=30,lock_fds=(fd,))
    finally:
        os.close(fd)


def test_parent_sigkill_closes_upstream_and_releases_inherited_lock(upstream,tmp_path):
    import fcntl
    call,options,entered,disconnected,requests=upstream; options['hold']=True
    lock=tmp_path/'job.lock'
    parent=multiprocessing.get_context('spawn').Process(target=orphan_driver,args=(payload(call),lock))
    parent.start()
    try:
        assert entered.wait(4)
        probe=os.open(lock,os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            parent.kill(); parent.join(3)
            assert not parent.is_alive() and disconnected.wait(3)
            deadline=time.monotonic()+3
            while True:
                try:
                    fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    assert time.monotonic()<deadline, 'orphan worker retained admission'
                    time.sleep(.02)
            assert len(requests)==1
        finally:
            os.close(probe)
    finally:
        if parent.is_alive():
            parent.kill(); parent.join(3)


def test_snapshot_reconstruction_uses_no_vault_and_is_independent(saved,monkeypatch):
    root,_=saved; original=AdvisoryCall(root,request()); snapshot=original.snapshot()
    def forbidden(*args,**kwargs):
        raise AssertionError('worker read the vault')
    monkeypatch.setattr(CredentialStore,'load',forbidden)
    restored=AdvisoryCall.from_snapshot(snapshot)
    snapshot['body']['capsule']='changed'
    assert restored.body==original.body
    assert restored.credentials==original.credentials


@pytest.mark.parametrize('mutation',[
    lambda s:s['credentials'].update(unapproved='secret'),
    lambda s:s['configuration']['roles'].update(backups=['backup']),
    lambda s:s['configuration']['policy'].update(maxAttempts=2),
    lambda s:s['body'].update(expectedRevision=0),
    lambda s:s.update(schemaVersion=True),
    lambda s:s['credentials'].update(backup=None),
])
def test_snapshot_rejects_recipient_revision_and_privacy_drift(saved,mutation):
    root,_=saved; snapshot=copy.deepcopy(AdvisoryCall(root,request()).snapshot()); mutation(snapshot)
    with pytest.raises((StoreError,ValueError)):
        AdvisoryCall.from_snapshot(snapshot)
