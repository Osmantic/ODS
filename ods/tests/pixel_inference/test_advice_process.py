import asyncio
import io
import json
import os
from pathlib import Path
import struct
import sys
import threading
import time

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'bin'))
from pixel_provider.advice_frames import decode_frame,encode_frame,read_frame
from pixel_provider.advice_process import run_worker
from pixel_provider.store import MAX_BYTES,StoreError

pytestmark = pytest.mark.skipif(os.name != 'posix',reason='POSIX worker pipes')

PRELUDE = '''
import json,os,select,struct,sys,time
def exact(n):
    b=b''
    while len(b)<n:
        part=os.read(0,n-len(b))
        if not part: raise RuntimeError('input closed')
        b+=part
    return b
value=json.loads(exact(struct.unpack('!I',exact(4))[0]))
def reply(value):
    b=json.dumps(value).encode()
    frame=struct.pack('!I',len(b))+b
    while frame:
        count=os.write(1,frame[:97]); frame=frame[count:]
'''


def command(tmp_path,body):
    child=tmp_path/'child.py'
    child.write_text(PRELUDE+body)
    return [sys.executable,'-I','-B',str(child)]


def test_large_request_and_partial_response_keeps_stdin_open(tmp_path):
    value={'capsule':'x'*131072}
    cmd=command(tmp_path,"assert not select.select([0],[],[],.05)[0]\nreply(value)\n")
    assert run_worker(cmd,value,cancelled=lambda:False,deadline_seconds=5)==value


@pytest.mark.parametrize('raw',[
    b'',b'abc',struct.pack('!I',MAX_BYTES+1),struct.pack('!I',0),
    struct.pack('!I',3)+b'{}',encode_frame({})+b'x',
    struct.pack('!I',13)+b'{"a":1,"a":2}',
    struct.pack('!I',3)+b'[1]',struct.pack('!I',9)+b'{"x":NaN}',
])
def test_strict_frames(raw):
    with pytest.raises(StoreError):
        decode_frame(raw)


def test_short_read_stream_and_frame_limit():
    class Short(io.BytesIO):
        def read(self,size=-1):
            return super().read(min(size,3))
    assert read_frame(Short(encode_frame({'ok':True})))=={'ok':True}
    with pytest.raises(StoreError):
        read_frame(io.BytesIO(b'\0\0'))
    with pytest.raises(StoreError):
        encode_frame({'text':'x'*MAX_BYTES})


@pytest.mark.parametrize('body',[
    "os.write(1,struct.pack('!I',262145)); time.sleep(10)\n",
    "reply({}); os.write(1,b'x'); time.sleep(10)\n",
    "os.write(2,b'x'*65536); time.sleep(10)\n",
    "os.write(1,b'\\0\\0'); sys.exit(0)\n",
    "reply({}); sys.exit(1)\n",
])
def test_malformed_flood_and_failed_process_bounded(tmp_path,body):
    started=time.monotonic()
    with pytest.raises(StoreError):
        run_worker(command(tmp_path,body),{},cancelled=lambda:False,deadline_seconds=4)
    assert time.monotonic()-started<3


@pytest.mark.parametrize('cancel',[True,False])
def test_cancel_and_timeout_reap_process(tmp_path,cancel):
    marker=tmp_path/'pid'
    cmd=command(tmp_path,f"open({str(marker)!r},'w').write(str(os.getpid()))\ntime.sleep(20)\n")
    started=time.monotonic()
    with pytest.raises(asyncio.CancelledError if cancel else StoreError):
        run_worker(cmd,{},cancelled=lambda:cancel and marker.exists(),deadline_seconds=1)
    assert time.monotonic()-started<3
    pid=int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid,0)


def test_no_ambient_secrets_and_request_not_in_argv(tmp_path,monkeypatch):
    secret='fictional-secret-098765'
    monkeypatch.setenv('OPENAI_API_KEY',secret)
    monkeypatch.setenv('PYTHONPATH','/fictional/untrusted')
    monkeypatch.setenv('HTTP_PROXY','http://untrusted.invalid')
    result=run_worker(command(tmp_path,"reply({'env':dict(os.environ),'argv':sys.argv,'secret':value['key']})\n"),
                      {'key':secret},cancelled=lambda:False,deadline_seconds=3)
    assert result['secret']==secret
    assert secret not in json.dumps(result['env'])+json.dumps(result['argv'])
    assert not {'OPENAI_API_KEY','PYTHONPATH','HTTP_PROXY'} & result['env'].keys()


def test_inherited_lock_survives_parent_descriptor_close(tmp_path):
    import fcntl
    lock=tmp_path/'lock'; ready=tmp_path/'ready'
    fd=os.open(lock,os.O_CREAT|os.O_RDWR,0o600)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    stop=threading.Event(); errors=[]
    cmd=command(tmp_path,f"open({str(ready)!r},'w').write('ready')\ntime.sleep(20)\n")
    def run():
        try:
            run_worker(cmd,{},cancelled=stop.is_set,deadline_seconds=5,lock_fds=(fd,))
        except asyncio.CancelledError:
            pass
        except BaseException as error:
            errors.append(error)
    thread=threading.Thread(target=run); thread.start()
    try:
        deadline=time.monotonic()+3
        while not ready.exists() and time.monotonic()<deadline:
            time.sleep(.01)
        assert ready.exists()
        os.close(fd); fd=None
        probe=os.open(lock,os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            stop.set(); thread.join(3)
            assert not thread.is_alive() and not errors
            fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:
            os.close(probe)
    finally:
        stop.set(); thread.join(3)
        if fd is not None:
            os.close(fd)


def test_cancel_before_spawn_does_not_execute(tmp_path):
    marker=tmp_path/'not-created'
    with pytest.raises(asyncio.CancelledError):
        run_worker(command(tmp_path,f"open({str(marker)!r},'w').close()\n"),{},
                   cancelled=lambda:True,deadline_seconds=2)
    assert not marker.exists()
