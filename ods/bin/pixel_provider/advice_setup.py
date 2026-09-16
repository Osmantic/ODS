"""Owner-confirmed setup jobs. No paths/commands are accepted from HTTP clients."""
import asyncio
import hashlib
import os
from pathlib import Path
import socket
import sys
import threading

from .advice import JOB_ID
from .advice_jobs import _sync_dir
from .advice_process import run_worker
from .advice_python import candidates,select_candidate
from .advice_runtime import HEX,RuntimeStore,runtime_status,source_digest
from .client import _json,_write_private,read_private
from .store import ProviderStore,StoreError,decode_document

FIELDS={'requestId','expectedRevision','sourceSha256','candidateId','confirmed'}


def validate_start(body):
    if (not isinstance(body,dict) or set(body)!=FIELDS
            or not isinstance(body['requestId'],str) or not JOB_ID.fullmatch(body['requestId'])
            or type(body['expectedRevision']) is not int or not 0<=body['expectedRevision']<2**53-1
            or any(not isinstance(body[k],str) or not HEX.fullmatch(body[k]) for k in ('sourceSha256','candidateId'))
            or body['confirmed'] is not True):
        raise StoreError('invalid-setup-request')
    return dict(body)


class SetupPointer(ProviderStore):
    config_name='latest-setup.json'
    def __init__(self,directory):
        def validate(value):
            if (set(value)!={'revision','jobId'} or value['jobId'] is not None and
                    (not isinstance(value['jobId'],str) or not JOB_ID.fullmatch(value['jobId']))):
                raise ValueError('invalid-setup-pointer')
            return value
        super().__init__(directory,validator=validate,default_factory=lambda:dict(revision=0,jobId=None))


class SetupJobs:
    def __init__(self,directory,*,runner=run_worker):
        self.providers=Path(directory).absolute()
        self.root=self.providers/'advice-setup-jobs'
        self.runner=runner
        self.mutex=threading.Lock()
        self.threads={}

    def _job(self,job_id):
        if not isinstance(job_id,str) or not JOB_ID.fullmatch(job_id):
            raise StoreError('invalid-setup-job')
        return self.root/job_id

    def status(self,job_id):
        import fcntl
        path=self._job(job_id)
        with ProviderStore(self.providers)._locked(False),ProviderStore(self.root)._locked(False):
            with ProviderStore(path)._locked(False) as directory_fd:
                claim=decode_document(read_private(path/'claim.json'))
                final=None
                if (path/'result.json').exists() or (path/'result.json').is_symlink():
                    final=decode_document(read_private(path/'result.json'))
                if final is not None:
                    return {**claim['metadata'],**final}
                fd=ProviderStore(path)._open_file(directory_fd,'running.lock')
                try:
                    try:
                        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        state='interrupted'
                    except BlockingIOError:
                        state='cancelling' if (path/'cancel.json').exists() else 'running'
                finally:
                    os.close(fd)
                # A parent may die after pointer commit but before its terminal
                # receipt. Reconcile the exact candidate, never replay the job.
                current=RuntimeStore(self.providers).load()
                committed=current['runtime'] and current['runtime']['id']==claim['metadata']['runtimeId']
                if committed:
                    state='completed' if runtime_status(self.providers)['status']=='ready' else 'failed'
                return {**claim['metadata'],'status':state,'error':'setup-drift' if state=='failed' else None}

    def latest(self):
        if not self.root.exists() and not self.root.is_symlink():
            return None
        pointer=SetupPointer(self.root).load()
        return self.status(pointer['jobId']) if pointer['jobId'] else None

    def start(self,body):
        import fcntl
        body=validate_start(body); path=self._job(body['requestId'])
        fingerprint=hashlib.sha256(_json(body)).hexdigest()
        with self.mutex,ProviderStore(self.providers)._locked(False):
            self.root.mkdir(mode=0o700,exist_ok=True)
            with SetupPointer(self.root)._locked(True) as root_fd:
                if path.exists() or path.is_symlink():
                    with ProviderStore(path)._locked(False):
                        old=decode_document(read_private(path/'claim.json'))
                        if old['fingerprint']!=fingerprint:
                            raise StoreError('setup-request-conflict')
                    # Release root lock before using public status path.
                else:
                    current=runtime_status(self.providers)
                    if current['revision']!=body['expectedRevision'] or source_digest()!=body['sourceSha256']:
                        raise StoreError('stale-revision')
                    candidate=select_candidate(body['candidateId'])
                    slot=ProviderStore(self.root)._open_file(root_fd,'install.lock',create=True)
                    running=None
                    try:
                        try:
                            fcntl.flock(slot,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        except BlockingIOError:
                            raise StoreError('setup-busy') from None
                        path.mkdir(mode=0o700)
                        _sync_dir(self.root)
                        metadata=dict(jobId=body['requestId'],expectedRevision=body['expectedRevision'],
                            runtimeId=current['runtimeId'] if current['status']=='ready' else 'runtime-'+body['requestId'].replace('-',''),
                            candidateId=candidate['id'])
                        with ProviderStore(path)._locked(True) as directory_fd:
                            running=ProviderStore(path)._open_file(directory_fd,'running.lock',create=True)
                            fcntl.flock(running,fcntl.LOCK_EX|fcntl.LOCK_NB)
                            _write_private(path/'claim.json',_json(dict(fingerprint=fingerprint,metadata=metadata)))
                            _sync_dir(path)
                        pointer=SetupPointer(self.root)._load(root_fd)
                        SetupPointer(self.root)._commit(root_fd,dict(revision=pointer['revision'],jobId=body['requestId']),pointer['revision'])
                        self.threads={k:t for k,t in self.threads.items() if t.is_alive()}
                        thread=threading.Thread(target=self._work,args=(path,body,running,slot),daemon=True,name='ods-advice-setup')
                        self.threads[body['requestId']]=thread
                        thread.start()
                    except BaseException:
                        if running is not None: os.close(running)
                        os.close(slot)
                        raise
        return self.status(body['requestId'])

    def _work(self,path,body,running,slot):
        def cancelled():
            marker=path/'cancel.json'
            if not marker.exists() and not marker.is_symlink(): return False
            if decode_document(read_private(marker))!={'cancel':True}:
                raise StoreError('invalid-cancellation-state')
            return True
        result=dict(status='failed',error='setup-failed')
        try:
            command=[str(Path(sys.executable).absolute()),'-I','-S','-B',str(Path(__file__).absolute().with_name('advice_setup_worker.py'))]
            value=self.runner(command,dict(schemaVersion=1,requestId=body['requestId'],directory=str(self.providers),
                candidateId=body['candidateId'],expectedRevision=body['expectedRevision'],sourceSha256=body['sourceSha256'],
                lockFds=[running,slot]),cancelled=cancelled,deadline_seconds=180,lock_fds=(running,slot))
            if (set(value)!={'schemaVersion','requestId','result'} or value['schemaVersion']!=1
                    or value['requestId']!=body['requestId'] or value['result'].get('status')!='ready'):
                raise StoreError('invalid-setup-result')
            # A valid child reply alone is not proof of publication. The final
            # block below accepts only the matching authoritative pointer.
        except asyncio.CancelledError:
            result=dict(status='cancelled',error=None)
        except Exception:
            pass
        finally:
            try:
                # The publishing pointer wins over a late cancellation/network
                # error, but only for this exact job's candidate identity.
                claim=decode_document(read_private(path/'claim.json'))
                current=RuntimeStore(self.providers).load()
                if current['runtime'] and current['runtime']['id']==claim['metadata']['runtimeId']:
                    ready=runtime_status(self.providers)['status']=='ready'
                    result=dict(status='completed' if ready else 'failed',error=None if ready else 'setup-drift')
                elif cancelled():
                    result=dict(status='cancelled',error=None)
                with ProviderStore(path)._locked(True):
                    _write_private(path/'result.json',_json(result)); _sync_dir(path)
            finally:
                os.close(running); os.close(slot)

    def cancel(self,job_id):
        path=self._job(job_id)
        with ProviderStore(self.providers)._locked(False),ProviderStore(self.root)._locked(False):
            with ProviderStore(path)._locked(True):
                if not (path/'result.json').exists():
                    try:
                        _write_private(path/'cancel.json',_json({'cancel':True})); _sync_dir(path)
                    except FileExistsError:
                        pass
        return self.status(job_id)


def readiness(directory):
    if os.name!='posix':
        return dict(status='unsupported',revision=0,runtimeId=None,sourceSha256=None,candidates=[],job=None,host=socket.gethostname())
    root=Path(directory).absolute()
    if not root.exists() and not root.is_symlink():
        return dict(status='not-configured',revision=0,runtimeId=None,sourceSha256=None,candidates=[],job=None,host=socket.gethostname())
    status=runtime_status(root)
    return dict(**status,sourceSha256=source_digest(),candidates=candidates(),job=SetupJobs(root).latest(),host=socket.gethostname())


_managers={}
_mutex=threading.Lock()
def get_setup_manager(data_dir):
    key=str(Path(data_dir).absolute()/'pixel-providers')
    with _mutex:
        if key not in _managers: _managers[key]=SetupJobs(key)
        return _managers[key]
