"""One tools-free inference call, controlled by a private parent-owned pipe.

EOF/extra input cancels the call and terminates a stuck worker within two
seconds, including on macOS where Linux PDEATHSIG is unavailable. No listeners.
"""
import asyncio
import os
import resource
from pathlib import Path
import select
import sys
import threading

# Invoked by a custody-checked absolute path with Python -I -B. The package path
# is explicit; neither the caller's CWD nor PYTHONPATH becomes executable input.
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from pixel_provider.advice import AdvisoryCall
from pixel_provider.advice_frames import encode_frame,read_frame


async def execute(value):
    if set(value) != {'schemaVersion','requestId','snapshot'} or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1:
        raise ValueError('invalid-worker-request')
    call = AdvisoryCall.from_snapshot(value['snapshot'])
    if value['requestId'] != call.body['requestId']:
        raise ValueError('invalid-worker-request')
    cancelled = threading.Event()
    done = threading.Event()

    def watch_parent():
        try:
            # The parent deliberately keeps its write end open after the frame.
            # EOF means parent loss/cancel; any data means an invalid second frame.
            os.read(0,1)
        finally:
            cancelled.set()
            if not done.wait(2):
                os._exit(125)

    if select.select([0],[],[],0)[0]:
        raise ValueError('worker-parent-disconnected')
    watcher = threading.Thread(target=watch_parent,daemon=True,name='advice-parent-watch')
    watcher.start()
    try:
        result = await call.execute(cancelled=cancelled.is_set)
        if cancelled.is_set():
            raise asyncio.CancelledError()
        return dict(schemaVersion=1,requestId=call.body['requestId'],result=result)
    finally:
        done.set()


def main():
    if os.name != 'posix' or sys.version_info < (3,11):
        return 2
    try:
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        # Unbuffered IO prevents read-ahead swallowing a forbidden second frame.
        with os.fdopen(os.dup(0),'rb',buffering=0) as stream:
            value = read_frame(stream)
        if sys.argv[1:] == ['--check']:
            import fastapi
            import httpx
            import uvicorn
            result = dict(schemaVersion=1,ready=True,python=list(sys.version_info[:3]),
                          fastapi=fastapi.__version__,httpx=httpx.__version__,uvicorn=uvicorn.__version__)
            sys.stdout.buffer.write(encode_frame(result))
            sys.stdout.buffer.flush()
            return 0
        if sys.argv[1:]:
            raise ValueError('invalid-worker-arguments')
        result = asyncio.run(execute(value))
        sys.stdout.buffer.write(encode_frame(result))
        sys.stdout.buffer.flush()
        return 0
    except (Exception,asyncio.CancelledError):
        # No traceback, provider body or capsule/key appears on stderr.
        sys.stderr.write('advice-worker-failed\n')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
