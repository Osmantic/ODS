"""Durable request identity with cancellable, owner-only advisory jobs.

Unfinished jobs are never replayed after process loss. No transcript is read;
only the explicitly submitted capsule enters the in-memory inference request.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import threading

from .advice import JOB_ID,validate_request
from .advice_call import WorkerAdvisoryCall
from .client import _json,_write_private,read_private
from .store import ProviderStore,StoreError,decode_document


def _sync_dir(path):
    fd = os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class AdvisoryJobs:
    def __init__(self,directory,*,call_factory=WorkerAdvisoryCall):
        self.providers = Path(directory)
        self.root = self.providers/'advice-jobs'
        self.call_factory = call_factory
        self.threads = {}
        self.mutex = threading.Lock()

    def _job(self,job_id):
        if not isinstance(job_id,str) or not JOB_ID.fullmatch(job_id):
            raise StoreError('invalid-advice-job')
        return self.root/job_id

    def _custody(self):
        # Reuse the POSIX directory/lock custody boundary, not path.resolve().
        with ProviderStore(self.providers)._locked(False):
            with ProviderStore(self.root)._locked(False):
                pass

    def _status(self,path):
        with ProviderStore(path)._locked(False) as directory_fd:
            claim = decode_document(read_private(path/'claim.json'))
            try:
                final = decode_document(read_private(path/'result.json'))
            except StoreError:
                if (path/'result.json').exists() or (path/'result.json').is_symlink():
                    raise
                final = None
            if final is not None:
                return {**claim['metadata'],**final}
            import fcntl
            fd = ProviderStore(path)._open_file(directory_fd,'running.lock',create=False)
            try:
                try:
                    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    state = 'interrupted'
                except BlockingIOError:
                    state = 'cancelling' if (path/'cancel.json').exists() else 'running'
            finally:
                os.close(fd)
            return {**claim['metadata'],'status':state,'result':None,'error':None}

    def status(self,job_id):
        self._custody()
        return self._status(self._job(job_id))

    def start(self,body):
        body = validate_request(body)
        fingerprint = hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        path = self._job(body['requestId'])
        with self.mutex,ProviderStore(self.providers)._locked(False):
            try:
                self.root.mkdir(mode=0o700)
                _sync_dir(self.providers)
            except FileExistsError:
                pass
            with ProviderStore(self.root)._locked(True) as root_fd:
                if path.exists() or path.is_symlink():
                    with ProviderStore(path)._locked(False):
                        claim = decode_document(read_private(path/'claim.json'))
                        if claim['fingerprint'] != fingerprint:
                            raise StoreError('advice-request-conflict')
                    return self._status(path)
                # Do not create durable admission until validation/credential
                # snapshot succeeds. The shared root lock serializes hosts.
                call = self.call_factory(self.providers,body)
                # Fixed stable OS locks bound total concurrency across all
                # cooperating host-agent processes, not just this instance.
                import fcntl
                slot_fd = lock_fd = None
                for slot in range(2):
                    candidate_fd = ProviderStore(self.root)._open_file(root_fd,f'slot-{slot}.lock',create=True)
                    try:
                        fcntl.flock(candidate_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        slot_fd = candidate_fd
                        break
                    except BlockingIOError:
                        os.close(candidate_fd)
                if slot_fd is None:
                    raise StoreError('advice-busy')
                try:
                    path.mkdir(mode=0o700)
                    _sync_dir(self.root)
                    with ProviderStore(path)._locked(True) as directory_fd:
                        lock_fd = ProviderStore(path)._open_file(directory_fd,'running.lock',create=True)
                        fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        _write_private(path/'claim.json',_json(dict(fingerprint=fingerprint,metadata=call.metadata())))
                        _sync_dir(path)
                    self.threads = {key:value for key,value in self.threads.items() if value.is_alive()}
                    thread = threading.Thread(target=self._work,args=(path,call,lock_fd,slot_fd),daemon=True,name='ods-pixel-advice')
                    self.threads[body['requestId']] = thread
                    thread.start()
                except BaseException:
                    if lock_fd is not None:
                        os.close(lock_fd)
                    os.close(slot_fd)
                    raise
        return self.status(body['requestId'])

    def _work(self,path,call,lock_fd,slot_fd):
        def cancelled():
            if not ((path/'cancel.json').exists() or (path/'cancel.json').is_symlink()):
                return False
            if decode_document(read_private(path/'cancel.json')) != {'cancel':True}:
                raise StoreError('invalid-cancellation-state')
            return True
        result = dict(status='failed',result=None,error='advice-failed')
        try:
            answer = call.run(cancelled=cancelled,lock_fds=(lock_fd,slot_fd))
            result = dict(status='completed',result=answer,error=None)
        except asyncio.CancelledError:
            result = dict(status='cancelled',result=None,error=None)
        except ImportError:
            result['error'] = 'advice-dependencies-missing'
        except Exception:
            pass
        finally:
            try:
                with ProviderStore(path)._locked(True):
                    if cancelled():
                        result = dict(status='cancelled',result=None,error=None)
                    _write_private(path/'result.json',_json(result))
                    _sync_dir(path)
            finally:
                os.close(lock_fd)
                os.close(slot_fd)

    def cancel(self,job_id):
        self._custody()
        path = self._job(job_id)
        with ProviderStore(path)._locked(True):
            if not (path/'result.json').exists():
                try:
                    _write_private(path/'cancel.json',_json({'cancel':True}))
                    _sync_dir(path)
                except FileExistsError:
                    pass
        return self.status(job_id)


_managers = {}
_manager_lock = threading.Lock()


def get_manager(data_dir):
    key = str(Path(data_dir).absolute())
    with _manager_lock:
        if key not in _managers:
            _managers[key] = AdvisoryJobs(Path(key)/'pixel-providers')
        return _managers[key]
