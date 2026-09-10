"""Explicit optional advisory venv, separate from the stdlib ODS host agent.

The owner can alter owner-owned files; hashes detect drift, not a malicious
same-UID adversary. Old/failed directories are retained, never auto-activated.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import uuid

from .advice_process import reap_group,run_worker,worker_environment
from .store import ProviderStore,StoreError,_private

REQUIREMENTS = 'fastapi>=0.109.0,<0.120.0\nhttpx>=0.27.0,<0.29.0\nuvicorn>=0.27.0,<0.30.0\n'
HEX = re.compile(r'[a-f0-9]{64}')
RUNTIME = re.compile(r'runtime-[a-f0-9]{32}')
SOURCE = Path(__file__).absolute().parent


def validate_receipt(value):
    if (not isinstance(value,dict) or set(value) != {'schemaVersion','revision','runtime'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or type(value['revision']) is not int):
        raise ValueError('invalid-runtime-receipt')
    runtime = value['runtime']
    if runtime is not None and (
            not isinstance(runtime,dict)
            or set(runtime) != {'id','sourceSha256','treeSha256','interpreter','interpreterSha256'}
            or not isinstance(runtime['id'],str) or not RUNTIME.fullmatch(runtime['id'])
            or any(not isinstance(runtime[k],str) or not HEX.fullmatch(runtime[k])
                   for k in ('sourceSha256','treeSha256','interpreterSha256'))
            or not isinstance(runtime['interpreter'],str) or not os.path.isabs(runtime['interpreter'])):
        raise ValueError('invalid-runtime-receipt')
    return value


class RuntimeStore(ProviderStore):
    config_name = 'advice-runtime.json'
    def __init__(self,directory):
        super().__init__(directory,validator=validate_receipt,
                         default_factory=lambda:dict(schemaVersion=1,revision=0,runtime=None))


def digest_file(path):
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid not in (0,os.geteuid())
            or before.st_mode & 0o022 or before.st_nlink != 1):
        raise StoreError('advice-runtime-unsafe')
    fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        if (os.fstat(fd).st_dev,os.fstat(fd).st_ino) != (before.st_dev,before.st_ino):
            raise StoreError('advice-runtime-drift')
        digest = hashlib.sha256()
        with os.fdopen(fd,'rb',closefd=False) as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                digest.update(chunk)
        after = os.fstat(fd)
        if (before.st_size,before.st_mtime_ns,before.st_ctime_ns) != (after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise StoreError('advice-runtime-drift')
        return digest.hexdigest()
    finally:
        os.close(fd)


def source_digest(directory=SOURCE):
    if directory.is_symlink() or not directory.is_dir():
        raise StoreError('advice-runtime-unsafe')
    digest = hashlib.sha256()
    files = sorted(directory.glob('*.py'))
    if not files or not (directory/'advice_worker.py').is_file():
        raise StoreError('advice-runtime-missing')
    for path in files:
        digest.update((path.name+'\0'+digest_file(path)+'\n').encode())
    return digest.hexdigest()


def private_directory(path):
    if not _private(path.lstat(),directory=True):
        raise StoreError('advice-runtime-unsafe')


def tree_digest(root,interpreter):
    private_directory(root)
    digest = hashlib.sha256()
    for directory,dirs,files in os.walk(root,followlinks=False):
        dirs.sort()
        for name in sorted(dirs+files):
            path = Path(directory)/name
            metadata = path.lstat()
            relative = path.relative_to(root).as_posix()
            if metadata.st_uid != os.geteuid():
                raise StoreError('advice-runtime-unsafe')
            if path.is_symlink():
                resolved = path.resolve(strict=True)
                allowed_python = relative in ('venv/bin/python','venv/bin/python3',
                                              'venv/bin/'+interpreter.name)
                if resolved != interpreter or not allowed_python:
                    try:
                        resolved.relative_to(root)
                    except ValueError:
                        raise StoreError('advice-runtime-unsafe') from None
                content = 'link:'+os.readlink(path)
            elif metadata.st_mode & 0o022:
                raise StoreError('advice-runtime-unsafe')
            elif stat.S_ISREG(metadata.st_mode):
                content = digest_file(path)
            elif stat.S_ISDIR(metadata.st_mode):
                content = 'directory'
            else:
                raise StoreError('advice-runtime-unsafe')
            digest.update((relative+'\0'+str(stat.S_IMODE(metadata.st_mode))+'\0'+content+'\n').encode())
    return digest.hexdigest()


def resolve_runtime(directory,*,receipt=None):
    directory = Path(directory).absolute()
    receipt = RuntimeStore(directory).load() if receipt is None else validate_receipt(receipt)
    runtime = receipt['runtime']
    if runtime is None:
        raise StoreError('advice-runtime-missing')
    root = Path(directory)/'advice-runtimes'
    private_directory(root)
    leaf = root/runtime['id']
    interpreter = Path(runtime['interpreter'])
    if (source_digest() != runtime['sourceSha256']
            or digest_file(interpreter) != runtime['interpreterSha256']
            or tree_digest(leaf,interpreter) != runtime['treeSha256']
            or source_digest(leaf/'source'/'pixel_provider') != runtime['sourceSha256']):
        raise StoreError('advice-runtime-drift')
    return [str(leaf/'venv'/'bin'/'python'),'-I','-B',str(leaf/'source'/'pixel_provider'/'advice_worker.py')]


def runtime_status(directory):
    receipt = RuntimeStore(directory).load()
    if receipt['runtime'] is None:
        return dict(status='missing',revision=receipt['revision'],runtimeId=None)
    try:
        resolve_runtime(directory,receipt=receipt)
        status = 'ready'
    except (StoreError,OSError):
        status = 'drift'
    return dict(status=status,revision=receipt['revision'],runtimeId=receipt['runtime']['id'])


def install_command(command,*,timeout=180,cancelled=lambda:False,lock_fds=(),inherited_group=False):
    if cancelled():
        raise asyncio.CancelledError()
    process = subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,start_new_session=not inherited_group,close_fds=True,
                               pass_fds=tuple(lock_fds),
                               cwd='/',umask=0o077,env={**worker_environment(),'PIP_CONFIG_FILE':os.devnull})
    try:
        deadline=time.monotonic()+timeout
        while process.poll() is None:
            if cancelled():
                raise asyncio.CancelledError()
            if time.monotonic()>=deadline:
                raise StoreError('advice-runtime-install-timeout')
            time.sleep(.05)
        if cancelled():
            raise asyncio.CancelledError()
        if process.returncode != 0:
            raise StoreError('advice-runtime-install-failed')
    finally:
        reap_group(process,inherited_group=inherited_group)


def prepare_runtime(directory,*,python,expected_revision,confirmed,cancelled=lambda:False,
                    lock_fds=(),inherited_group=False,runtime_id=None):
    deadline=time.monotonic()+180
    def checkpoint():
        if cancelled():
            raise asyncio.CancelledError()
        if time.monotonic()>=deadline:
            raise StoreError('advice-runtime-install-timeout')
    def install(command,*,timeout=180):
        checkpoint()
        return install_command(command,timeout=min(timeout,max(1,int(deadline-time.monotonic()))),
            cancelled=cancelled,lock_fds=lock_fds,inherited_group=inherited_group)
    checkpoint()
    if os.name != 'posix':
        raise StoreError('unsupported-platform')
    if confirmed is not True:
        raise StoreError('runtime-install-confirmation-required')
    if not isinstance(python,str) or not os.path.isabs(python):
        raise StoreError('explicit-python-required')
    if runtime_id is not None and (not isinstance(runtime_id,str) or not RUNTIME.fullmatch(runtime_id)):
        raise StoreError('invalid-runtime-id')
    directory = Path(directory).absolute()
    store = RuntimeStore(directory)
    initial = store.load()
    if type(expected_revision) is not int or initial['revision'] != expected_revision:
        raise StoreError('stale-revision')
    if initial['runtime'] is not None and runtime_status(directory)['status'] == 'ready':
        return runtime_status(directory)
    interpreter = Path(python).resolve(strict=True)
    interpreter_hash = digest_file(interpreter)
    # Validate Python before attempting a venv or creating an inactive runtime.
    install([str(interpreter),'-I','-B','-c',
                     'import sys; assert sys.version_info >= (3,11)'],timeout=10)
    root = Path(directory)/'advice-runtimes'
    with store._locked(True):
        root.mkdir(mode=0o700,exist_ok=True)
        private_directory(root)
    with ProviderStore(root)._locked(True):
        if store.load()['revision'] != expected_revision:
            raise StoreError('stale-revision')
        checkpoint()
        leaf = root/(runtime_id or ('runtime-'+uuid.uuid4().hex))
        leaf.mkdir(mode=0o700)
        source_hash = source_digest()
        package = leaf/'source'/'pixel_provider'
        package.parent.mkdir(mode=0o700)
        package.mkdir(mode=0o700)
        for source in SOURCE.glob('*.py'):
            digest_file(source)
            shutil.copyfile(source,package/source.name)
            (package/source.name).chmod(0o600)
        if source_digest(package) != source_hash:
            raise StoreError('advice-runtime-drift')
        install([str(interpreter),'-I','-B','-m','venv',str(leaf/'venv')])
        binary = str(leaf/'venv'/'bin'/'python')
        requirements = leaf/'requirements.txt'
        requirements.write_text(REQUIREMENTS,encoding='utf-8')
        requirements.chmod(0o600)
        report = leaf/'install-report.json'
        install([binary,'-I','-B','-m','pip','--isolated','--disable-pip-version-check',
                         'install','--no-input','--no-cache-dir','--only-binary=:all:',
                         '--index-url','https://pypi.org/simple','--report',str(report),'-r',str(requirements)])
        if report.stat().st_size > 2*1024*1024:
            raise StoreError('advice-runtime-install-failed')
        installed = json.loads(report.read_text(encoding='utf-8'))
        locked = []
        for item in installed['install']:
            name,version = item['metadata']['name'],item['metadata']['version']
            digest = item['download_info']['archive_info']['hashes']['sha256']
            if (not re.fullmatch(r'[A-Za-z0-9_.-]+',name) or not re.fullmatch(r'[A-Za-z0-9_.+!-]+',version)
                    or not HEX.fullmatch(digest)):
                raise StoreError('advice-runtime-install-failed')
            locked.append(name+'=='+version+' --hash=sha256:'+digest)
        lock = leaf/'requirements.lock'
        lock.write_text('\n'.join(sorted(locked))+'\n',encoding='utf-8')
        lock.chmod(0o600)
        report.chmod(0o600)
        command = [binary,'-I','-B',str(package/'advice_worker.py')]
        checkpoint()
        check = run_worker(command+['--check'],{},cancelled=cancelled,
            deadline_seconds=min(30,max(1,int(deadline-time.monotonic()))),lock_fds=lock_fds,
            inherited_group=inherited_group)
        if check.get('ready') is not True or check.get('schemaVersion') != 1:
            raise StoreError('advice-runtime-selftest-failed')
        if source_digest() != source_hash or digest_file(interpreter) != interpreter_hash:
            raise StoreError('advice-runtime-drift')
        candidate = dict(schemaVersion=1,revision=expected_revision,runtime=dict(
            id=leaf.name,sourceSha256=source_hash,treeSha256=tree_digest(leaf,interpreter),
            interpreter=str(interpreter),interpreterSha256=interpreter_hash))
        # Flush all installed bytes before atomically publishing the pointer.
        for directory_path,_,files in os.walk(leaf,followlinks=False):
            checkpoint()
            for name in files:
                path = Path(directory_path)/name
                if not path.is_symlink():
                    with path.open('rb') as stream:
                        os.fsync(stream.fileno())
            fd = os.open(directory_path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        fd = os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        # Cancellation after this commit must be reconciled as published, not
        # falsely reported as a rollback. The caller knows the candidate ID.
        checkpoint()
        committed = store.save(candidate,expected_revision=expected_revision)
        resolve_runtime(directory,receipt=committed)
        return dict(status='ready',revision=committed['revision'],runtimeId=leaf.name)
