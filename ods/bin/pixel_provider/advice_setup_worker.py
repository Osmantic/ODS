"""One stdlib setup operation in its own parent-supervised process group."""
import asyncio
import os
from pathlib import Path
import resource
import select
import signal
import sys

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from pixel_provider.advice import JOB_ID
from pixel_provider.advice_frames import encode_frame,read_frame
from pixel_provider.advice_python import select_candidate
from pixel_provider.advice_runtime import prepare_runtime,source_digest
from pixel_provider.store import StoreError


def main():
    if os.name!='posix' or os.getpgrp()!=os.getpid():
        return 2
    try:
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        with os.fdopen(os.dup(0),'rb',buffering=0) as stream:
            value=read_frame(stream)
        if (set(value)!={'schemaVersion','requestId','directory','candidateId','expectedRevision','sourceSha256','lockFds'}
                or type(value['schemaVersion']) is not int or value['schemaVersion']!=1
                or not isinstance(value['requestId'],str) or not JOB_ID.fullmatch(value['requestId'])
                or not isinstance(value['directory'],str) or not os.path.isabs(value['directory'])
                or not isinstance(value['lockFds'],list) or len(value['lockFds'])!=2
                or any(type(fd) is not int or fd<3 for fd in value['lockFds'])):
            raise ValueError('invalid-setup-frame')
        for fd in value['lockFds']:
            os.fstat(fd)
        if select.select([0],[],[],0)[0]:
            raise ValueError('parent-disconnected')
        # A thread dies with this worker, possibly leaving a pip grandchild
        # behind. A separate watchdog retains the inherited slot locks and
        # survives worker exit until the supervisor closes its private pipe.
        # Close output descriptors so it cannot prevent normal result EOF.
        group=os.getpgrp()
        if os.fork()==0:
            os.close(1); os.close(2)
            try:
                os.read(0,1)
            finally:
                os.killpg(group,signal.SIGKILL)
                os._exit(1)
        if source_digest()!=value['sourceSha256']:
            raise StoreError('advice-runtime-drift')
        candidate=select_candidate(value['candidateId'])
        result=prepare_runtime(value['directory'],python=candidate['path'],expected_revision=value['expectedRevision'],
            confirmed=True,lock_fds=tuple(value['lockFds']),inherited_group=True,
            runtime_id='runtime-'+value['requestId'].replace('-',''))
        sys.stdout.buffer.write(encode_frame(dict(schemaVersion=1,requestId=value['requestId'],result=result)))
        sys.stdout.buffer.flush()
        return 0
    except (Exception,asyncio.CancelledError):
        sys.stderr.write('advice-setup-failed\n')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
