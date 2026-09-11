"""Bounded private worker transport; stdlib-only on the ODS host interpreter."""
import asyncio
from contextlib import suppress
import os
import selectors
import signal
import struct
import subprocess
import time

from .advice_frames import decode_frame,encode_frame
from .store import MAX_BYTES,StoreError


def worker_environment():
    # No ambient proxy, Python injection, cloud key or session variables.
    return {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}


def reap_group(process,*,inherited_group=False):
    """Own group only, including descendants retaining pipes after leader exit."""
    if inherited_group:
        # The outer setup supervisor owns this group and its EOF watchdog kills
        # all descendants if it loses its parent. Never signal our own group here.
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            process.kill()
        process.wait(timeout=2)
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid,signal.SIGTERM)
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    with suppress(ProcessLookupError):
        os.killpg(process.pid,signal.SIGKILL)
    process.wait(timeout=2)


def run_worker(command,snapshot,*,cancelled,deadline_seconds,lock_fds=(),inherited_group=False):
    if os.name != 'posix':
        raise StoreError('unsupported-platform')
    if (not isinstance(command,list) or not command or not os.path.isabs(command[0])
            or any(not isinstance(arg,str) or '\0' in arg for arg in command)
            or type(deadline_seconds) is not int or not 1 <= deadline_seconds <= 180):
        raise StoreError('invalid-worker-request')
    pending = memoryview(encode_frame(snapshot))
    if cancelled():
        raise asyncio.CancelledError()
    deadline = time.monotonic()+deadline_seconds
    process = None
    output = bytearray()
    error_bytes = 0
    expected = None
    try:
        process = subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,bufsize=0,close_fds=True,
                                   pass_fds=tuple(lock_fds),start_new_session=not inherited_group,
                                   env=worker_environment(),cwd='/')
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdin,process.stdout,process.stderr):
                os.set_blocking(stream.fileno(),False)
                selector.register(stream,selectors.EVENT_WRITE if stream is process.stdin else selectors.EVENT_READ)
            while True:
                if cancelled():
                    raise asyncio.CancelledError()
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise StoreError('advice-worker-deadline')
                # stdin remains open but unregistered after its one frame. Its
                # EOF is a liveness/cancellation signal, not a frame delimiter.
                readers = any(key.fileobj is not process.stdin for key in selector.get_map().values())
                if not readers and process.poll() is not None:
                    if process.returncode != 0 or pending:
                        raise StoreError('advice-worker-failed')
                    return decode_frame(bytes(output))
                for key,_ in selector.select(min(.05,remaining)):
                    stream = key.fileobj
                    if stream is process.stdin:
                        try:
                            count = os.write(stream.fileno(),pending[:65536])
                        except BlockingIOError:
                            continue
                        if count <= 0:
                            raise StoreError('advice-worker-failed')
                        pending = pending[count:]
                        if not pending:
                            selector.unregister(stream)
                        continue
                    try:
                        part = os.read(stream.fileno(),65536)
                    except BlockingIOError:
                        continue
                    if not part:
                        selector.unregister(stream)
                    elif stream is process.stderr:
                        error_bytes += len(part)
                        if error_bytes > 8192:
                            raise StoreError('advice-worker-output-limit')
                    else:
                        output.extend(part)
                        if expected is None and len(output) >= 4:
                            size = struct.unpack('!I',output[:4])[0]
                            if not 0 < size <= MAX_BYTES:
                                raise StoreError('advice-worker-output-limit')
                            expected = size+4
                        if len(output) > MAX_BYTES+4 or expected is not None and len(output) > expected:
                            raise StoreError('advice-worker-output-limit')
    except (OSError,subprocess.SubprocessError):
        raise StoreError('advice-worker-failed') from None
    finally:
        if process is not None:
            with suppress(OSError):
                process.stdin.close()
            try:
                reap_group(process,inherited_group=inherited_group)
            finally:
                for stream in (process.stdout,process.stderr):
                    stream.close()
