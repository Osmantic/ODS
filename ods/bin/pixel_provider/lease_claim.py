"""Persistent no-replay identity, separate from live OS-lock admission slots."""
import hashlib
import os
from pathlib import Path
import re

from .client import _json,_write_private
from .store import ProviderStore,StoreError

RUN_ID = re.compile(r'(?:chatcmpl_)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',re.I)


def sync_directory(path):
    fd = os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class LeaseClaim:
    def __init__(self,directory,run_id,session_id,revision):
        if not isinstance(run_id,str) or not RUN_ID.fullmatch(run_id):
            raise StoreError('provider-run-invalid')
        if not isinstance(session_id,str) or not session_id or len(session_id)>256:
            raise StoreError('provider-session-invalid')
        if type(revision) is not int or not 0<=revision<=2**53-1:
            raise StoreError('provider-revision-invalid')
        self.root = Path(directory).absolute()/'route-leases'
        self.run_id,self.session_id,self.revision = run_id.lower(),session_id,revision
        self.path = self.root/self.run_id
        self._fd = None

    def __enter__(self):
        if self._fd is not None:
            raise StoreError('provider-lease-already-held')
        import fcntl
        with ProviderStore(self.root.parent)._locked(False):
            try:
                self.root.mkdir(mode=0o700)
            except FileExistsError:
                pass
            # Every entrant syncs the parent, including a concurrent mkdir loser.
            sync_directory(self.root.parent)
            with ProviderStore(self.root)._locked(True) as root_fd:
                if self.path.exists() or self.path.is_symlink():
                    raise StoreError('provider-run-replayed')
                # Consume valid identities even when admission is busy. A late
                # retry cannot turn an earlier terminal refusal into new work.
                self.path.mkdir(mode=0o700)
                os.fsync(root_fd)
                _write_private(self.path/'claim.json',_json(dict(schemaVersion=1,
                    runId=self.run_id,sessionIdHash=hashlib.sha256(self.session_id.encode()).hexdigest(),
                    revision=self.revision)))
                sync_directory(self.path)
                for name in ('slot-0.lock','slot-1.lock'):
                    fd = ProviderStore(self.root)._open_file(root_fd,name,create=True)
                    try:
                        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        self._fd = fd
                        break
                    except BlockingIOError:
                        os.close(fd)
                    except BaseException:
                        os.close(fd)
                        raise
                if self._fd is None:
                    raise StoreError('provider-lease-busy')
        return self

    def finish(self,status,events=()):
        if self._fd is None or status not in ('closed','failed','deadline'):
            raise StoreError('provider-lease-status-invalid')
        # Do not persist free-form upstream response fields (even model labels).
        fields = {'requestId','revision','providerId','result','attempt','upstreamStatus'}
        metadata = [{key:value for key,value in item.items() if key in fields} for item in events]
        _write_private(self.path/'result.json',_json(dict(status=status,events=metadata)))
        sync_directory(self.path)

    def __exit__(self,*_args):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        return False
