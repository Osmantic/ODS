"""Actual Linux Popen/pipes/raw clock, not Darwin/Seatbelt/Root dispatch proof."""
# ruff: noqa: E402
import copy
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time

import pytest

if sys.platform != 'linux' or not Path('/proc/self/stat').is_file():
    pytest.skip('Linux proc/pipe mechanics only', allow_module_level=True)
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1]/'bin')]
from test_native_source_child import identity, tree
from pixel_connections import custody as s

BOOT='01234567-89AB-4DEF-8123-0123456789AB'
GEN='1'*32
def clock(): return time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
def invocation(*, admission=.8, worker=2, role='sync', nonce='2'*32, identifier=None):
    now=clock()
    return s._native_invocation({'role':role,'identifier':identifier,'nonce':nonce,'boot':BOOT,
        'clock':'darwin-clock-monotonic-raw-ns-v1','generation':GEN,'issuedNs':str(now),
        'admissionDeadlineNs':str(now+int(admission*10**9)),'workerDeadlineNs':str(now+int(worker*10**9))})
def label(inv):
    doc=inv.document
    return s.NATIVE_LABEL+doc['role']+('.'+doc['identifier'] if doc['identifier'] else '')
def slots(inv):
    return {'generation':GEN,'boot':BOOT,'phase':'active','invocations':{label(inv):{'phase':'prepared','invocation':inv.document}}}
def publish(path,value):
    candidate=path.with_suffix('.next')
    candidate.write_text(json.dumps(value))
    candidate.replace(path)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(s,'_native_clock_ns',clock)  # Explicit Linux clock platform seam.
    processes=[]
    descriptors=[]
    scope=tmp_path/'invented-slots.json'
    def launch(inv, *, mode='barrier', after='', reset=True):
        if reset:publish(scope,slots(inv))
        ready_r,ready_w=os.pipe()
        go_r,go_w=os.pipe()
        descriptors.extend((fd,(info.st_dev,info.st_ino,info.st_mode)) for fd,info in
            ((ready_r,os.fstat(ready_r)),(go_w,os.fstat(go_w))))
        marker=tmp_path/('after-go-'+str(len(processes)))
        provider=tmp_path/('invented_provider_'+str(len(processes))+'.py')
        provider.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("invented module imported")\n')
        code=f'''import importlib.util,json,os,sys,time
from pathlib import Path
sys.path.insert(0,{str(Path(s.__file__).resolve().parents[1])!r})
from pixel_connections import custody as s
s._native_clock_ns=lambda:time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
inv=s._native_invocation(json.loads({json.dumps(inv.document)!r}))
def current():return json.loads(Path({str(scope)!r}).read_text())
try:
    MODE={mode!r}
    if MODE=='wrong':
        os.write({ready_w},b'{{"version":1,"kind":"ready","invocationSha256":"wrong"}}\\n')
        os.read({go_r},256)
        sys.exit(3)
    if MODE=='eof':sys.exit(4)
    if MODE=='held':time.sleep(.3)
    s._native_child_ready_barrier(inv,ready_fd={ready_w},go_fd={go_r},current=current)
except Exception as error:
    print(type(error).__name__+':'+getattr(error,'code','closed'),file=sys.stderr)
    sys.exit(2)
spec=importlib.util.spec_from_file_location('invented_provider',{str(provider)!r})
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
{after}
'''
        started=time.monotonic()
        process=subprocess.Popen([sys.executable,'-I','-B','-c',code],cwd=tmp_path,
            env={'HOME':str(tmp_path),'TMPDIR':str(tmp_path),'PATH':'/usr/bin:/bin'},
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,pass_fds=(ready_w,go_r))
        processes.append(process)
        os.close(ready_w)
        os.close(go_r)
        return process,started,ready_r,go_w,marker
    yield launch,scope
    for fd,binding in descriptors:
        s._native_pipe_close(fd,binding)
    for process in processes:
        if process.poll() is None:process.kill()  # Exact fixture-owned Popen, never a UID/name kill.
        process.wait(timeout=3)
        process.stderr.close()


def release(row, inv, scope, **changes):
    process,started,ready,go,_marker=row
    return s._native_release_child(process,started=started,invocation=inv,ready_fd=ready,go_fd=go,
        current=changes.pop('current',lambda:json.loads(scope.read_text())),uid=os.getuid(),gid=os.getgid(),
        executable=str(Path(sys.executable).resolve()),inspect=changes.pop('inspect',identity),tree=tree,**changes)
def supervise(witness, inv, scope, **kwargs):
    return s._native_supervise_child(witness,role=inv.document['role'],timeout=s.NATIVE_CHILD_BUDGETS[inv.document['role']],
        _invocation=inv,_current=lambda:json.loads(scope.read_text()),**kwargs)


def test_ready_is_held_before_marker_then_go_and_owned_reap(world):
    launch,scope=world
    inv=invocation()
    row=launch(inv)
    assert select.select([row[2]],[],[],1)[0]
    assert not row[4].exists()  # READY is buffered; real child waits for GO.
    result=release(row,inv,scope)
    assert result.go_sent and result.witness.process is row[0]
    done=supervise(result.witness,inv,scope)
    assert done.reaped and done.returncode==0 and row[4].exists()


@pytest.mark.parametrize('mode',['wrong','eof'])
def test_bad_ready_or_eof_never_releases_and_reaps_owned_child(world,mode):
    launch,scope=world
    inv=invocation()
    row=launch(inv,mode=mode)
    result=release(row,inv,scope)
    assert not result.go_sent and result.cleanup.reaped and not row[4].exists()


def test_expired_admission_does_not_borrow_still_valid_worker_budget(world):
    launch,scope=world
    inv=invocation(admission=.08,worker=2)
    row=launch(inv,mode='held')
    time.sleep(.12)
    result=release(row,inv,scope)
    assert clock()<int(inv.document['workerDeadlineNs'])
    assert not result.go_sent and result.cleanup.reaped and not row[4].exists()


def test_async_worker_continues_after_start_admission_expires(world):
    launch,scope=world
    inv=invocation(admission=.5,worker=2)
    row=launch(inv,after='time.sleep(.6)')
    result=release(row,inv,scope)
    assert result.go_sent
    done=supervise(result.witness,inv,scope)
    assert clock()>int(inv.document['admissionDeadlineNs'])
    assert done.reaped and done.returncode==0


def test_cancel_while_held_closes_go_and_never_signals_unrelated_identity(world):
    launch,scope=world
    inv=invocation()
    row=launch(inv,mode='held')
    event=threading.Event()
    event.set()
    result=release(row,inv,scope,cancel=event)
    assert not result.go_sent and result.cleanup.reaped and not row[4].exists()


def test_identity_failure_closes_go_without_sending_to_wrong_process(world):
    launch,scope=world
    inv=invocation()
    row=launch(inv)
    result=release(row,inv,scope,inspect=lambda _pid:identity(os.getpid()))
    assert not result.go_sent and result.cleanup.reaped and result.cleanup.disposition=='unknown'
    assert not row[4].exists()


def test_sibling_slot_does_not_change_a_but_replacement_a_refuses(world):
    launch,scope=world
    inv=invocation(worker=4)
    row=launch(inv,after='time.sleep(.7)')
    released=release(row,inv,scope)
    assert released.go_sent
    other=invocation(role='action',identifier='calendar-1893456789012-12abcdef',admission=2,worker=2,nonce='3'*32)
    snapshot=slots(inv)
    snapshot['invocations'][label(other)]={'phase':'prepared','invocation':other.document}
    publish(scope,snapshot)
    sibling=launch(other,reset=False)
    original=copy.deepcopy(snapshot['invocations'][label(inv)])
    assert s._native_slot_matches(snapshot,inv)
    second=release(sibling,other,scope)
    assert second.go_sent and second.witness.process is not released.witness.process
    assert supervise(second.witness,other,scope).reaped
    assert supervise(released.witness,inv,scope).returncode==0
    assert row[4].exists() and sibling[4].exists()
    assert snapshot['invocations'][label(inv)]==original
    changed=copy.deepcopy(snapshot)
    changed['invocations'][label(inv)]['invocation']['nonce']='4'*32
    assert not s._native_slot_matches(changed,inv)


def test_scope_changed_after_ready_observation_prevents_go(world):
    launch,scope=world
    inv=invocation()
    row=launch(inv)
    calls=[]
    def current():
        value=slots(inv)
        calls.append(True)
        if len(calls)>1:value['phase']='quiescing'
        return value
    result=release(row,inv,scope,current=current)
    assert not result.go_sent and result.cleanup.reaped and not row[4].exists()


def test_absolute_deadline_includes_setup_and_term_kill_reap(world):
    launch,scope=world
    inv=invocation(admission=.7,worker=1.4)
    row=launch(inv,after='import signal\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\ntime.sleep(30)')
    result=release(row,inv,scope)
    assert result.go_sent
    time.sleep(.3)
    done=supervise(result.witness,inv,scope)
    assert done.reaped and done.returncode==-signal.SIGKILL
    assert clock()<int(inv.document['workerDeadlineNs'])+100_000_000


def test_invocation_snapshots_detach_mutation_and_no_cli_admission(world):
    inv=invocation()
    value=inv.document
    value['nonce']='4'*32
    assert inv.document['nonce']=='2'*32
    for role,identifier in [('sync',None),('direct',None),('action','calendar-1893456789012-12abcdef'),
            ('reconcile','gmail-draft-1893456789012-34abcdef'),('exchange','5'*32),('revoke',None)]:
        with pytest.raises(s.CustodyError,match='native-source-broker-platform-required'):
            s._native_child_main(['--native-broker-child',role,*([identifier] if identifier else [])])


def test_bad_go_frame_is_rejected_by_actual_child_before_marker(world):
    launch,scope=world
    inv=invocation()
    row=launch(inv)
    ready=s._native_pipe_binding(row[2],writing=False)
    assert s._native_pipe_transfer(row[2],ready,deadline=int(inv.document['admissionDeadlineNs']))==s._native_barrier_frame(inv,'ready')
    bound=s._native_observe_child(row[0],started=row[1],uid=os.getuid(),gid=os.getgid(),
        executable=str(Path(sys.executable).resolve()),inspect=identity,tree=tree)
    os.write(row[3],b'wrong GO\n')
    os.close(row[3])
    done=supervise(bound,inv,scope)
    assert done.reaped and done.returncode==2 and not row[4].exists()


def test_unknown_slot_cannot_be_resubmitted_as_prepared(world):
    launch,scope=world
    inv=invocation()
    row=launch(inv,mode='held')
    snapshot=slots(inv)
    snapshot['invocations'][label(inv)]['phase']='unknown'
    publish(scope,snapshot)
    before=scope.read_bytes()
    result=release(row,inv,scope)
    assert not result.go_sent and result.cleanup.reaped and not row[4].exists()
    assert scope.read_bytes()==before  # Read-only mechanics cannot reopen/renew.


def test_fd_reuse_does_not_close_unrelated_regular_file(world,tmp_path):
    read,write=os.pipe()
    binding=s._native_pipe_binding(read,writing=False)
    os.close(read)
    target=os.open(tmp_path/'unrelated',os.O_CREAT|os.O_RDWR,0o600)
    try:
        if target!=read:
            os.dup2(target,read)
        s._native_pipe_close(read,binding)
        os.write(read,b'intact')
        assert (tmp_path/'unrelated').read_bytes()==b'intact'
    finally:
        os.close(read)
        if target!=read:os.close(target)
        os.close(write)


@pytest.mark.parametrize('transition', ['quiescing', 'running'])
def test_child_rechecks_own_scope_after_go_before_invented_import(world, transition):
    launch,scope=world
    inv=invocation()
    row=launch(inv)
    calls=[]
    def stale_parent_view():
        calls.append(True)
        if len(calls)==2:
            changed=slots(inv)
            if transition == 'running':
                changed['invocations'][label(inv)]['phase']='running'
            else:
                changed['phase']='quiescing'
            publish(scope,changed)
        return slots(inv)
    result=release(row,inv,scope,current=stale_parent_view)
    assert result.go_sent  # Means GO transmitted, not effect/child acceptance.
    row[0].wait(timeout=1)
    if transition == 'running':
        assert row[0].returncode==0 and row[4].exists()
    else:
        assert row[0].returncode==2 and not row[4].exists()
        assert b'CustodyError:' in row[0].stderr.read()


@pytest.mark.parametrize('change',['scope','deadline'])
def test_after_select_rechecks_scope_and_deadline_before_go_write(world,monkeypatch,change):
    launch,scope=world
    inv=invocation(admission=.3,worker=2)
    row=launch(inv)
    original=select.select
    held=[]
    def wait(read,write,error,timeout):
        result=original(read,write,error,timeout)
        if row[3] in write and not held:
            held.append(True)
            if change=='scope':
                changed=slots(inv)
                changed['phase']='quiescing'
                publish(scope,changed)
            else:
                time.sleep(max(0,int(inv.document['admissionDeadlineNs'])-clock())/10**9+.02)
        return result
    monkeypatch.setattr(select,'select',wait)
    result=release(row,inv,scope)
    assert held and not result.go_sent and result.cleanup.reaped and not row[4].exists()


def test_oversized_encoded_invocation_refuses_before_json_parse(monkeypatch):
    bad=s._NativeInvocation(b' '*1025,s._NATIVE_SEAL)
    monkeypatch.setattr(s.json,'loads',lambda *_a,**_k:pytest.fail('must bound before parse'))
    with pytest.raises(s.CustodyError,match='invocation-invalid'):
        s._native_invocation_document(bad)


def test_native_clock_requires_darwin_without_linux_fixture_seam():
    with pytest.raises(s.CustodyError,match='clock-unavailable'):
        s._native_clock_ns()
