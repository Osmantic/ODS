"""Real private worker listeners, OS locks, process loss and durable refusal."""
import contextlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

from test_provider_session import saved  # shared private provider fixture
from pixel_provider.advice_frames import encode_frame,read_frame
from pixel_provider.advice_process import worker_environment
from pixel_provider.lease_claim import LeaseClaim
from pixel_provider import lease_claim
from pixel_provider.store import StoreError
from pixel_provider.vault import CredentialStore

BIN = Path(__file__).resolve().parents[2]/'bin'
pytestmark = pytest.mark.skipif(os.name!='posix',reason='POSIX lease ownership')


def request(**changes):
    return dict(schemaVersion=1,runId='chatcmpl_'+str(uuid.uuid4()),sessionId=str(uuid.uuid4()),
        expectedRevision=1,allowCloud=False,confirmed=True,timeoutSeconds=30,**changes)


def launch(root,body):
    child = subprocess.Popen([sys.executable,'-I','-B',str(BIN/'pixel_provider/route_worker.py'),
        '--provider-directory',str(root)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,bufsize=0,env=worker_environment())
    child.stdin.write(encode_frame(body)); child.stdin.flush()
    return child


def ready(child):
    assert select.select([child.stdout],[],[],6)[0], 'worker did not respond'
    try: return read_frame(child.stdout)
    except StoreError:
        child.wait(timeout=2)
        raise AssertionError(child.stderr.read().decode()) from None


def close(child):
    if child.stdin and not child.stdin.closed:
        child.stdin.close()
    try: child.wait(timeout=5)
    except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=3); raise
    child.stdout.close(); child.stderr.close()


def port_closed(lease):
    port = int(lease['baseUrl'].split(':')[-1].split('/')[0])
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=.1): return False
    except OSError: return True


def process_live(pid):
    try: return Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[0]!='Z'
    except FileNotFoundError: return False


def test_claim_consumed_and_live_slot_released(saved):
    root,_=saved; body=request()
    with LeaseClaim(root,body['runId'],body['sessionId'],1) as claim:
        claim.finish('closed')
    with pytest.raises(StoreError,match='provider-run-replayed'):
        with LeaseClaim(root,body['runId'],body['sessionId'],1): pass


def test_incomplete_claim_denies_replay_without_consuming_a_live_slot(saved,monkeypatch):
    root,_=saved; body=request()
    with monkeypatch.context() as patch:
        patch.setattr(lease_claim,'_write_private',lambda *_: (_ for _ in ()).throw(OSError('write fault')))
        with pytest.raises(StoreError):
            with LeaseClaim(root,body['runId'],body['sessionId'],1): pass
    assert (root/'route-leases'/body['runId']).is_dir()
    with pytest.raises(StoreError,match='provider-run-replayed'):
        with LeaseClaim(root,body['runId'],body['sessionId'],1): pass
    with LeaseClaim(root,str(uuid.uuid4()),'a',1):
        with LeaseClaim(root,str(uuid.uuid4()),'b',1): pass


def test_unsafe_slot_fails_closed_instead_of_using_another_slot(saved):
    root,_=saved
    with LeaseClaim(root,str(uuid.uuid4()),'initialize',1): pass
    slot=root/'route-leases'/'slot-0.lock'
    target=root/'provider-config.json'; original=target.read_bytes()
    slot.unlink(); slot.symlink_to(target)
    with pytest.raises(StoreError,match='unsafe-file'):
        with LeaseClaim(root,str(uuid.uuid4()),'denied',1): pass
    assert target.read_bytes()==original


def test_concurrent_duplicate_processes_emit_exactly_one_lease(saved):
    root,_=saved; body=request(); children=[launch(root,body),launch(root,body)]; leases=[]
    try:
        for child in children:
            assert select.select([child.stdout],[],[],6)[0]
            try: leases.append(read_frame(child.stdout)['lease'])
            except StoreError:
                child.wait(timeout=3); assert child.returncode!=0
        assert len(leases)==1
    finally:
        for child in children: close(child)


def test_lease_refuses_wrong_token_then_closes_and_cannot_replay(saved):
    root,_ = saved; body=request(); child=launch(root,body)
    try:
        lease=ready(child)['lease']; assert not port_closed(lease)
        response=httpx.post(lease['baseUrl']+'/chat/completions',json={},headers={'Authorization':'Bearer wrong'})
        assert response.status_code==401
        if sys.platform=='linux':
            for name in ('cmdline','environ'):
                assert lease['token'].encode() not in Path(f'/proc/{child.pid}/{name}').read_bytes()
    finally: close(child)
    assert port_closed(lease)
    metadata=(root/'route-leases'/body['runId']/'result.json').read_text()
    assert json.loads(metadata)['status']=='closed' and lease['token'] not in metadata
    replay=launch(root,body)
    try:
        replay.wait(timeout=4); assert replay.returncode!=0 and replay.stdout.read()==b''
    finally: close(replay)


@pytest.mark.parametrize('loss',['eof','extra','sigkill','sigterm','sigint','deadline'])
def test_process_loss_closes_listener_releases_slots_and_keeps_claim(saved,loss):
    root,_=saved; body=request(); body['timeoutSeconds']=1 if loss=='deadline' else 30
    child=launch(root,body)
    try:
        lease=ready(child)['lease']
        if loss=='eof': child.stdin.close()
        elif loss=='extra': child.stdin.write(b'x'); child.stdin.flush()
        elif loss=='sigkill': child.kill()
        elif loss in ('sigterm','sigint'): child.send_signal(signal.SIGTERM if loss=='sigterm' else signal.SIGINT)
        child.wait(timeout=5)
        assert port_closed(lease)
        if loss in ('sigterm','sigint'):
            assert child.returncode==0
            assert json.loads((root/'route-leases'/body['runId']/'result.json').read_text())['status']=='closed'
        with pytest.raises(StoreError,match='provider-run-replayed'):
            with LeaseClaim(root,body['runId'],body['sessionId'],1): pass
        with LeaseClaim(root,str(uuid.uuid4()),'fresh-one',1):
            with LeaseClaim(root,str(uuid.uuid4()),'fresh-two',1): pass
    finally: close(child)


def test_two_live_slots_and_busy_refusal_cannot_replay(saved):
    root,_=saved; a=launch(root,request()); b=launch(root,request()); denied=request()
    c=None
    try:
        la,lb=ready(a)['lease'],ready(b)['lease']
        assert la['baseUrl']!=lb['baseUrl'] and la['token']!=lb['token']
        c=launch(root,denied); c.wait(timeout=4)
        assert c.returncode!=0 and c.stdout.read()==b''
    finally:
        for child in (a,b,c):
            if child is not None: close(child)
    with pytest.raises(StoreError,match='provider-run-replayed'):
        with LeaseClaim(root,denied['runId'],denied['sessionId'],1): pass


@pytest.mark.parametrize('change',[{'expectedRevision':0},{'confirmed':False},{'allowCloud':'false'},
    {'timeoutSeconds':0},{'runId':'../unsafe'},{'sessionId':''}])
def test_invalid_or_stale_requests_have_no_listener(saved,change):
    root,_=saved; body=request(); body.update(change); child=launch(root,body)
    try:
        child.wait(timeout=5); assert child.returncode!=0 and child.stdout.read()==b''
    finally: close(child)


@pytest.mark.parametrize('loss',['eof','sigkill'])
def test_inflight_upstream_disconnect_and_replay_denial(saved,loss):
    root,config=saved; received=threading.Event(); disconnected=threading.Event(); finish=threading.Event()
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def do_POST(self):
            calls.append(self.rfile.read(int(self.headers['Content-Length'])))
            received.set()
            while not finish.is_set():
                if select.select([self.connection],[],[],.05)[0] and self.connection.recv(1,socket.MSG_PEEK)==b'':
                    disconnected.set(); return
    upstream=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server_thread=threading.Thread(target=upstream.serve_forever,daemon=True); server_thread.start()
    config['providers'][0]['baseUrl']=f'http://127.0.0.1:{upstream.server_port}/v1'
    config['roles']['backups']=[]
    CredentialStore(root).save_public(dict(document=config,expectedRevision=1,
        credentialChanges={'primary':dict(action='set',value='fixture-upstream-secret')}))
    body=request(); body['expectedRevision']=2; child=launch(root,body); client_thread=None
    try:
        lease=ready(child)['lease']
        def call():
            try:
                httpx.post(lease['baseUrl']+'/chat/completions',json=dict(model='ods/pixel',
                    messages=[dict(role='user',content='hold')]),
                    headers={'Authorization':'Bearer '+lease['token']},timeout=8)
            except httpx.HTTPError: pass
        client_thread=threading.Thread(target=call); client_thread.start()
        assert received.wait(3)
        if loss=='sigkill': child.kill()
        else: child.stdin.close()
        child.wait(timeout=5)
        assert disconnected.wait(3) and port_closed(lease)
        client_thread.join(timeout=3); assert not client_thread.is_alive()
        replay=launch(root,body)
        try:
            replay.wait(timeout=4); assert replay.returncode!=0 and replay.stdout.read()==b''
        finally: close(replay)
        assert len(calls)==1
    finally:
        close(child); finish.set(); upstream.shutdown(); upstream.server_close(); server_thread.join(2)
        if client_thread: client_thread.join(9)


@pytest.mark.skipif(sys.platform!='linux',reason='Linux orphan process state')
def test_supervisor_sigkill_closes_orphan_listener(saved,tmp_path):
    root,_=saved; body=request(); supervisor=tmp_path/'supervisor.py'
    supervisor.write_text('''
import os,subprocess,sys,time
sys.path.insert(0,sys.argv[1])
from pixel_provider.advice_frames import encode_frame,read_frame
from pixel_provider.advice_process import worker_environment
body=read_frame(sys.stdin.buffer)
child=subprocess.Popen([sys.executable,'-I','-B',sys.argv[1]+'/pixel_provider/route_worker.py',
  '--provider-directory',sys.argv[2]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
  env=worker_environment())
child.stdin.write(encode_frame(body)); child.stdin.flush()
value=read_frame(child.stdout); value['pid']=child.pid
sys.stdout.buffer.write(encode_frame(value)); sys.stdout.buffer.flush()
time.sleep(60)
''')
    parent=subprocess.Popen([sys.executable,'-I','-B',str(supervisor),str(BIN),str(root)],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=worker_environment())
    worker_pid=None
    try:
        parent.stdin.write(encode_frame(body)); parent.stdin.flush(); value=ready(parent)
        worker_pid=value['pid']; parent.kill(); parent.wait(timeout=2)
        deadline=time.monotonic()+5
        while process_live(worker_pid) and time.monotonic()<deadline: time.sleep(.02)
        assert not process_live(worker_pid) and port_closed(value['lease'])
        with LeaseClaim(root,str(uuid.uuid4()),'replacement-a',1):
            with LeaseClaim(root,str(uuid.uuid4()),'replacement-b',1): pass
        replay=launch(root,body)
        try:
            replay.wait(timeout=4); assert replay.returncode!=0 and replay.stdout.read()==b''
        finally: close(replay)
    finally:
        if parent.poll() is None: parent.kill()
        close(parent)
        if worker_pid and process_live(worker_pid):
            assert str(root).encode() in Path(f'/proc/{worker_pid}/cmdline').read_bytes()
            with contextlib.suppress(ProcessLookupError): os.kill(worker_pid,signal.SIGKILL)
