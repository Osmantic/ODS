import asyncio
import os
from pathlib import Path
import socket
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'bin'))
from pixel_provider.connection_tunnel import serve_tunnel,ssh_command
from pixel_provider.store import StoreError


def test_ssh_command_fixed_destination_and_auth():
    assert ssh_command('ssh','tower2-lan',4005) == ['ssh','-T','-o','BatchMode=yes','-o',
        'StrictHostKeyChecking=yes','-W','127.0.0.1:4005','tower2-lan']


@pytest.mark.parametrize('target,port',[('-Fconfig',4005),('name;exec',4005),('',4005),
    ('tower2',80),('tower2',65536),('tower2',True)])
def test_invalid_tunnel_target(target,port):
    with pytest.raises(StoreError,match='invalid-tunnel-target'):
        ssh_command('ssh',target,port)


@pytest.mark.skipif(os.name != 'posix',reason='POSIX test executable')
def test_real_half_close_and_owned_ssh_cleanup(tmp_path):
    fake = tmp_path/'ssh'
    fake.write_text('#!'+sys.executable+'\nimport sys,time\n'
        'data=sys.stdin.buffer.read()\n'
        'sys.stdout.buffer.write(b"reply:"+data)\nsys.stdout.buffer.flush()\n')
    fake.chmod(0o700)
    async def check():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
        stop,ready = asyncio.Event(),asyncio.Event()
        server = asyncio.create_task(serve_tunnel(ssh_bin=str(fake),target='fixture',remote_port=4005,
            listen_port=port,stop=stop,ready=ready.set))
        writer = None
        try:
            await asyncio.wait_for(ready.wait(),3)
            reader,writer = await asyncio.open_connection('127.0.0.1',port)
            writer.write(b'request'); await writer.drain(); writer.write_eof()
            assert await asyncio.wait_for(reader.read(),3) == b'reply:request'
        finally:
            if writer:
                writer.close(); await writer.wait_closed()
            stop.set(); await asyncio.wait_for(server,8)
        with pytest.raises(OSError):
            await asyncio.open_connection('127.0.0.1',port)
    asyncio.run(check())


@pytest.mark.skipif(os.name != 'posix',reason='POSIX test executable')
def test_stop_reaps_active_forwarding_process(tmp_path):
    fake = tmp_path/'ssh'
    fake.write_text('#!'+sys.executable+'\nimport os,sys\n'
        'print(os.getpid(),flush=True)\nsys.stdin.buffer.read()\n')
    fake.chmod(0o700)
    async def check():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
        stop,ready = asyncio.Event(),asyncio.Event()
        task = asyncio.create_task(serve_tunnel(ssh_bin=str(fake),target='fixture',remote_port=4005,
            listen_port=port,stop=stop,ready=ready.set))
        writer = None
        try:
            await asyncio.wait_for(ready.wait(),3)
            reader,writer = await asyncio.open_connection('127.0.0.1',port)
            pid = int(await asyncio.wait_for(reader.readline(),3))
            stop.set(); await asyncio.wait_for(task,8)
            assert await asyncio.wait_for(reader.read(),3) == b''
            with pytest.raises(ProcessLookupError):
                os.kill(pid,0)
        finally:
            stop.set(); await asyncio.wait_for(task,8)
            if writer:
                writer.close(); await writer.wait_closed()
    asyncio.run(check())


@pytest.mark.skipif(os.name != 'posix',reason='POSIX test executable')
def test_cancel_during_spawn_acquires_and_reaps_child(tmp_path,monkeypatch):
    fake = tmp_path/'ssh'
    fake.write_text('#!'+sys.executable+'\nimport sys\nsys.stdin.buffer.read()\n')
    fake.chmod(0o700)
    original = asyncio.create_subprocess_exec
    async def check():
        spawned,release,stop,ready = (asyncio.Event() for _ in range(4))
        children = []
        async def delayed(*args,**kw):
            child = await original(*args,**kw)
            children.append(child); spawned.set()
            await release.wait()
            return child
        monkeypatch.setattr(asyncio,'create_subprocess_exec',delayed)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
        task = asyncio.create_task(serve_tunnel(ssh_bin=str(fake),target='fixture',remote_port=4005,
            listen_port=port,stop=stop,ready=ready.set))
        writer = None
        try:
            await asyncio.wait_for(ready.wait(),3)
            reader,writer = await asyncio.open_connection('127.0.0.1',port)
            await asyncio.wait_for(spawned.wait(),3)
            stop.set(); await asyncio.sleep(0); await asyncio.sleep(0)
            release.set(); await asyncio.wait_for(task,8)
            assert children[0].returncode is not None
            assert await asyncio.wait_for(reader.read(),3) == b''
        finally:
            release.set(); stop.set()
            if writer:
                writer.close(); await writer.wait_closed()
            await asyncio.wait_for(task,8)
            for child in children:
                if child.returncode is None:
                    child.kill(); await child.wait()
    asyncio.run(check())
