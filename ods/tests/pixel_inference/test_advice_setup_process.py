"""Actual setup-worker parent loss with a long-lived installer grandchild."""
import contextlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

import pytest

BIN=Path(__file__).resolve().parents[2]/'bin'
sys.path.insert(0,str(BIN))
from pixel_provider.advice_frames import encode_frame

pytestmark=pytest.mark.skipif(sys.platform!='linux',reason='Linux process-state assertions')


def live(pid):
    try: return Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[0]!='Z'
    except FileNotFoundError: return False


def until(check,seconds=5):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if check(): return
        time.sleep(.02)
    assert check()


@pytest.mark.parametrize('loss',['eof','eof-staggered-kill','supervisor-sigkill','worker-exit','supervisor-success','supervisor-error'])
def test_parent_loss_reaps_installer_descendants_and_keeps_slot_until_exit(tmp_path,loss):
    import fcntl
    group_marker=tmp_path/'group'; descendant_marker=tmp_path/'descendant'
    wrapper=tmp_path/'worker.py'
    grandchild=f"import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); Path({str(descendant_marker)!r}).write_text(str(os.getpid())); time.sleep(60)"
    wrapper.write_text(f'''
import os,sys,signal,time
from pathlib import Path
sys.path.insert(0,{str(BIN)!r})
from pixel_provider import advice_setup_worker as worker
from pixel_provider.advice_runtime import install_command
Path({str(group_marker)!r}).write_text(str(os.getpid()))
worker.source_digest=lambda:'a'*64
worker.select_candidate=lambda identity:{{'path':sys.executable}}
if {loss!r}=='eof-staggered-kill':
    original_killpg=os.killpg
    def staggered_kill(group,signum):
        # Force the observed kernel ordering: watched PIDs exit before the
        # watchdog releases its inherited locks. This is fixture-only.
        os.kill(group,signum)
        os.kill(int(Path({str(descendant_marker)!r}).read_text()),signum)
        time.sleep(.5)
        original_killpg(group,signum)
    os.killpg=staggered_kill
def prepare(directory,**kwargs):
    code="import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',"+repr({grandchild!r})+"],pass_fds="+repr(tuple(kwargs['lock_fds']))+"); time.sleep({60 if loss in ('eof','eof-staggered-kill','supervisor-sigkill') else .5})"
    install_command([sys.executable,'-I','-S','-c',code],timeout=30,cancelled=kwargs.get('cancelled',lambda:False),lock_fds=kwargs['lock_fds'],inherited_group=True)
    if {loss!r}=='supervisor-success': return {{'status':'ready'}}
    raise AssertionError('installer unexpectedly completed')
worker.prepare_runtime=prepare
raise SystemExit(worker.main())
''')
    locks=[os.open(tmp_path/name,os.O_CREAT|os.O_RDWR,0o600) for name in ('running','slot')]
    for fd in locks: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    request=dict(schemaVersion=1,requestId=str(uuid.uuid4()),directory=str(tmp_path),candidateId='b'*64,
                 expectedRevision=0,sourceSha256='a'*64,lockFds=locks)
    command=[sys.executable,'-I','-S','-B',str(wrapper)]
    if loss.startswith('supervisor-'):
        supervisor=tmp_path/'supervisor.py'
        supervisor.write_text(f'''
import sys
sys.path.insert(0,{str(BIN)!r})
from pixel_provider.advice_process import run_worker
run_worker({command!r},{request!r},cancelled=lambda:False,deadline_seconds=40,lock_fds={tuple(locks)!r})
''')
        command=[sys.executable,'-I','-S','-B',str(supervisor)]
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             start_new_session=True,pass_fds=tuple(locks))
    group=None; descendant=None
    probe=os.open(tmp_path/'slot',os.O_RDWR)
    try:
        if not loss.startswith('supervisor-'):
            process.stdin.write(encode_frame(request)); process.stdin.flush()
        for fd in locks: os.close(fd)
        locks=[]
        until(lambda:descendant_marker.exists() and descendant_marker.stat().st_size>0)
        group=int(group_marker.read_text()); descendant=int(descendant_marker.read_text())
        assert live(group) and live(descendant)
        with pytest.raises(BlockingIOError): fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if loss=='worker-exit':
            process.wait(timeout=3)
            assert live(descendant)
            with pytest.raises(BlockingIOError): fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if not loss.startswith('supervisor-'): process.stdin.close()
        elif loss=='supervisor-sigkill': process.kill()
        process.wait(timeout=6)
        if loss=='supervisor-success': assert process.returncode==0
        elif loss=='supervisor-error': assert process.returncode!=0
        def cleanup_finished():
            if live(group) or live(descendant): return False
            # SIGKILL delivery is not simultaneous: the watchdog may retain
            # its inherited lock after these two processes have exited. Slot
            # acquisition, not their PIDs alone, proves cleanup completion.
            try: fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: return False
            return True
        until(cleanup_finished,seconds=6)
    finally:
        for fd in locks: os.close(fd)
        if group is None and group_marker.exists(): group=int(group_marker.read_text())
        if group is not None:
            with contextlib.suppress(ProcessLookupError): os.killpg(group,signal.SIGKILL)
        if process.poll() is None: process.kill()
        process.wait(timeout=3)
        process.stdin.close(); os.close(probe)
