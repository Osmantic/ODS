"""Owner-selected SSH forwarding to a peer's loopback inference port only."""
import asyncio
import contextlib
import json
import os
import re
import signal

from .store import StoreError


def ssh_command(binary, target, remote_port):
    if (not isinstance(target,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',target)
            or type(remote_port) is not int or not 1024 <= remote_port <= 65535):
        raise StoreError('invalid-tunnel-target')
    return [binary,'-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
            '-W',f'127.0.0.1:{remote_port}',target]


async def serve_tunnel(*, ssh_bin, target, remote_port, listen_port, stop=None, ready=None):
    command = ssh_command(ssh_bin,target,remote_port)
    if type(listen_port) is not int or not 1024 <= listen_port <= 65535:
        raise StoreError('invalid-tunnel-port')
    stop = stop or asyncio.Event()
    clients = set()
    closing = False

    async def handle(reader,writer):
        task = asyncio.current_task()
        if closing or len(clients) >= 8:
            writer.close()
            return
        clients.add(task)
        child = None
        pumps = []
        async def pump(source,destination):
            while True:
                data = await source.read(65536)
                if not data:
                    if destination.can_write_eof():
                        destination.write_eof()
                        await destination.drain()
                    return
                destination.write(data)
                await destination.drain()
        try:
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL))
            try:
                child = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                # Process creation may finish after cancellation. Acquire its
                # handle before unwinding so the owned child is still reaped.
                child = await spawn
                raise
            pumps = [asyncio.create_task(pump(reader,child.stdin)),asyncio.create_task(pump(child.stdout,writer))]
            async with asyncio.timeout(3600):
                done,_ = await asyncio.wait(pumps,return_when=asyncio.FIRST_COMPLETED)
                # A client may half-close its request and still await the reply.
                # EOF from SSH, however, ends the remote response direction.
                for item in done:
                    item.result()
                if pumps[1] not in done:
                    await pumps[1]
        except (OSError,TimeoutError,ConnectionError):
            pass  # Remote authentication/readiness is proven by `probe`, not bind.
        finally:
            for item in pumps:
                item.cancel()
            await asyncio.gather(*pumps,return_exceptions=True)
            writer.close()
            if child is not None:
                child.stdin.close()
                try:
                    await asyncio.wait_for(child.wait(),5)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        child.terminate()
                    try:
                        await asyncio.wait_for(child.wait(),2)
                    except TimeoutError:
                        with contextlib.suppress(ProcessLookupError):
                            child.kill()
                        await child.wait()
            clients.discard(task)

    server = await asyncio.start_server(handle,'127.0.0.1',listen_port)
    try:
        if ready:
            ready()
        await stop.wait()
    finally:
        closing = True
        server.close()
        active = list(clients)
        for task in active:
            task.cancel()
        await asyncio.gather(*active,return_exceptions=True)
        # Python 3.12 waits for active connections too: drain handlers FIRST.
        await server.wait_closed()


def run_tunnel(**options):
    if os.name != 'posix':
        raise StoreError('unsupported-platform')
    async def run():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for number in (signal.SIGTERM,signal.SIGINT):
            loop.add_signal_handler(number,stop.set)
        def ready():
            print(json.dumps({'status':'listening-not-probed','listen':'127.0.0.1:'+str(options['listen_port']),
                'target':options['target'],'remotePort':options['remote_port']}),flush=True)
        await serve_tunnel(**options,stop=stop,ready=ready)
    asyncio.run(run())
