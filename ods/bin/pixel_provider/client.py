"""Fresh, isolated Pixel client preparation through the exact pinned renderer.

Preparation does not modify an existing install, install services or change
privileges. Agent turns require an explicit run. Canonical inputs are retained.
"""
import hashlib
from contextlib import contextmanager, nullcontext
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import tarfile
import threading
import uuid

from .connection import normalize_connection
from .connection_transport import probe_connection
from .store import MAX_BYTES, StoreError, decode_document

PIXEL_COMMIT = 'bbd1d2d62c7260f822ba1e727728a0a02f78895f'
# Preparation follows the current paired installer. Loading must not rewrite or
# invalidate clients prepared with an earlier supported renderer. These exact
# receipt identities do not certify custody of an owner's writable source tree.
PREPARED_PIXEL_COMMITS = frozenset((
    PIXEL_COMMIT,
    '9409d1ae894394a4848bf5b41a6323e64c577f06',
    '70f44c90ac40b8409ebc965becc5b085a053e270',
))
OPENCLAW_VERSION = '2026.6.33'


def read_private(path):
    if os.name != 'posix':
        raise StoreError('unsupported-platform')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                    or info.st_mode & 0o077 or info.st_size > MAX_BYTES):
                raise StoreError('unsafe-client-file')
            value = handle.read(MAX_BYTES+1)
            if len(value) > MAX_BYTES:
                raise StoreError('unsafe-client-file')
            return value
    except OSError:
        raise StoreError('unsafe-client-file') from None


def _write_private(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'wb') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _json(value):
    return (json.dumps(value,indent=2,sort_keys=True)+'\n').encode()


def _command(args, *, env=None, timeout=30):
    try:
        value = subprocess.run(args,env=env,capture_output=True,timeout=timeout)
        if value.returncode:
            raise StoreError('client-preparation-failed')
        return value.stdout
    except (OSError, subprocess.SubprocessError):
        raise StoreError('client-preparation-failed') from None


def _pixel_archive(repository):
    raw = _command(['git','--no-replace-objects','-C',str(repository),'archive','--format=tar',PIXEL_COMMIT])
    if len(raw) > 64*1024*1024:
        raise StoreError('invalid-pixel-source')
    try:
        archive = tarfile.open(fileobj=io.BytesIO(raw))
        members = archive.getmembers()
    except tarfile.TarError:
        raise StoreError('invalid-pixel-source') from None
    if len(members) > 10000:
        raise StoreError('invalid-pixel-source')
    for member in members:
        path = Path(member.name)
        if (path.is_absolute() or '..' in path.parts or not (member.isdir() or member.isfile())
                or member.size > 16*1024*1024):
            raise StoreError('invalid-pixel-source')
    return archive


def _base_environment(directory):
    # Ambient Pixel/OpenClaw overrides must not select another profile, agent
    # directory, extension set or runtime while operating this isolated client.
    result = {name:value for name,value in os.environ.items()
              if not name.startswith(('PIXEL_','OPENCLAW_','XDG_'))}
    result.update(OPENCLAW_STATE_DIR=str(directory/'state'),
                  OPENCLAW_CONFIG_PATH=str(directory/'state/openclaw.json'),
                  XDG_CONFIG_HOME=str(directory/'xdg'),XDG_CACHE_HOME=str(directory/'cache'))
    return result


def _render_environment(source, directory):
    result = _base_environment(directory)
    for line in read_private(source/'.env').decode().splitlines():
        if not line or line.startswith('#'):
            continue
        name, separator, raw = line.partition('=')
        if not separator or not name.replace('_','').isalnum():
            raise StoreError('invalid-client-environment')
        values = shlex.split(raw)
        if len(values) != 1:
            raise StoreError('invalid-client-environment')
        result[name] = values[0]
    result.update(OPENCLAW_STATE_DIR=str(directory/'state'),
                  OPENCLAW_CONFIG_PATH=str(directory/'state/openclaw.json'),
                  XDG_CONFIG_HOME=str(directory/'xdg'),XDG_CACHE_HOME=str(directory/'cache'))
    return result


def prepare_client(connection, directory, *, confirmed_endpoint, pixel_repository,
                   openclaw_bin, reasoning, sandbox_image='openclaw-sandbox:bookworm-slim'):
    if os.name != 'posix':
        raise StoreError('unsupported-platform')
    if type(reasoning) is not bool:
        raise StoreError('reasoning-choice-required')
    if not isinstance(sandbox_image,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}',sandbox_image):
        raise StoreError('invalid-sandbox-image')
    directory = Path(directory).absolute()
    if directory.exists() or directory.is_symlink():
        raise StoreError('client-directory-exists')
    executable = Path(openclaw_bin)
    if not executable.is_absolute() or not executable.is_file():
        raise StoreError('invalid-openclaw-runtime')
    version = _command([str(executable),'--version']).decode()
    if not version.startswith('OpenClaw '+OPENCLAW_VERSION+' '):
        raise StoreError('unsupported-openclaw-version')
    connection = normalize_connection(connection)
    metadata = probe_connection(connection,confirmed_endpoint=confirmed_endpoint)
    archive = _pixel_archive(pixel_repository)
    # A failed/partial preparation stays private for inspection. Never replace
    # it or infer permission to reuse someone else's existing client directory.
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        raise StoreError('client-directory-exists') from None
    directory = directory.resolve(strict=True)
    source = directory/'pixel-source'
    source.mkdir(mode=0o700)
    for member in archive.getmembers():
        target = source/member.name
        if member.isdir():
            target.mkdir(parents=True,exist_ok=True,mode=0o700)
        else:
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with archive.extractfile(member) as handle:
                _write_private(target,handle.read())
            if member.mode & 0o111:
                target.chmod(0o700)
    archive.close()
    agent_id = 'pixel-client-'+uuid.uuid4().hex[:16]
    answers = dict(deploymentProfile='prepared',capabilityProfile='minimal',
        ownerName='ODS Owner',organization='Local ODS',deploymentName=agent_id,timeZone='UTC',
        agentId=agent_id,agentName='Assistant',openclawBin=str(executable),openclawHome=str(directory/'state'),
        installDir=str(directory/'installation'),workspace=str(directory/'state/workspace'),
        modelProvider='ods-peer',modelId='ods/shared',modelName='ODS '+connection['label'],
        modelBaseUrl=connection['baseUrl'],modelApiKey=connection['credential']['apiKey'],
        modelReasoning=reasoning,modelContextWindow=metadata['contextLength'],
        modelMaxTokens=min(4096,metadata['maxOutputTokens']),modelPrivateHosts=[],
        embeddingCache=str(directory/'cache/embeddings'),gatewayPort=18789,
        emailLimbEnabled=False,calendarLimbEnabled=False,socialLimbEnabled=False,
        webLimbEnabled=False,operationsLimbEnabled=False,frontierLimbEnabled=False,
        gatewayExtensions=[],agentSkills=[],localCapabilityPacks=[],
        frontierAuthMode='api-key',frontierBudgetProfile='starter')
    _write_private(directory/'connection.json',_json(connection))
    _write_private(directory/'onboarding.json',_json(answers))
    env = _base_environment(directory)
    _command(['node',str(source/'scripts/configure.mjs'),'--answers',str(directory/'onboarding.json')],env=env)
    # Persist an explicitly selected image in the canonical generated env too,
    # so a later renderer run cannot silently restore a different image.
    original_env = read_private(source/'.env').decode()
    lines = original_env.splitlines()
    if sum(line.startswith('PIXEL_SANDBOX_IMAGE=') for line in lines) != 1:
        raise StoreError('invalid-client-environment')
    changed_env = '\n'.join('PIXEL_SANDBOX_IMAGE='+shlex.quote(sandbox_image)
        if line.startswith('PIXEL_SANDBOX_IMAGE=') else line for line in lines)+'\n'
    _write_private(source/'.env.prepared',changed_env.encode())
    os.replace(source/'.env.prepared',source/'.env')
    runtime_env = _render_environment(source,directory)
    # Explicit renderer override for a previously installed sandbox image; this
    # does not build, retag or replace the shared image or alter its privileges.
    runtime_env['PIXEL_SANDBOX_IMAGE'] = sandbox_image
    (directory/'state').mkdir(mode=0o700)
    _command(['node',str(source/'scripts/render-config.mjs'),str(directory/'state/openclaw.json')],env=runtime_env)
    shutil.copytree(source/'.generated/workspace',directory/'state/workspace')
    configuration = decode_document(read_private(directory/'state/openclaw.json'))
    if configuration['agents']['defaults']['sandbox']['mode'] != 'all':
        raise StoreError('invalid-client-sandbox')
    tracked = ('connection.json','onboarding.json','pixel-source/.env','state/openclaw.json')
    record = dict(schemaVersion=1,status='prepared-not-activated',pixelCommit=PIXEL_COMMIT,
        openclawVersion=OPENCLAW_VERSION,agentId=agent_id,execution='client-owned',
        sandboxImage=sandbox_image,metadata=metadata,
        files={name:hashlib.sha256(read_private(directory/name)).hexdigest() for name in tracked})
    _write_private(directory/'prepared.json',_json(record))
    return {'status':record['status'],'directory':str(directory),'agentId':agent_id,
            'model':metadata['routedModel'],'configuration':str(directory/'state/openclaw.json'),
            'workspace':str(directory/'state/workspace')}


def load_client(directory):
    if os.name != 'posix':
        raise StoreError('unsupported-platform')
    directory = Path(directory).absolute()
    try:
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise StoreError('unsafe-client-directory')
        for name in ('pixel-source','state'):
            info = (directory/name).lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise StoreError('unsafe-client-directory')
        record = decode_document(read_private(directory/'prepared.json'))
        if (type(record.get('schemaVersion')) is not int or record.get('schemaVersion') != 1
                or not isinstance(record.get('pixelCommit'),str)
                or record['pixelCommit'] not in PREPARED_PIXEL_COMMITS
                or record.get('openclawVersion') != OPENCLAW_VERSION
                or record.get('status') != 'prepared-not-activated' or record.get('execution') != 'client-owned'
                or not isinstance(record.get('agentId'),str)
                or not re.fullmatch(r'pixel-client-[a-f0-9]{16}',record['agentId'])
                or set(record['files']) != {'connection.json','onboarding.json','pixel-source/.env','state/openclaw.json'}):
            raise StoreError('invalid-client-record')
        for name,digest in record['files'].items():
            if hashlib.sha256(read_private(directory/name)).hexdigest() != digest:
                raise StoreError('client-configuration-changed')
        return directory,record
    except (KeyError,TypeError,AttributeError,OSError):
        raise StoreError('invalid-client-record') from None


@contextmanager
def _run_lock(directory):
    import fcntl
    fd = os.open(directory/'.client-run.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise StoreError('unsafe-client-lock')
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise StoreError('client-busy') from None
        yield fd
    finally:
        os.close(fd)  # Keep the stable lock inode; never unlink it on release.


def run_client(directory, message, *, timeout=240, _adapter=None):
    """One explicit embedded turn; no global gateway or service activation."""
    directory,record = load_client(directory)
    with _run_lock(directory) as run_fd:
        return _run_locked(directory,record,message,timeout=timeout,_adapter=_adapter,_lock_fd=run_fd)


def _run_locked(directory,record,message,*,timeout,_adapter,_lock_fd):
    if threading.current_thread() is not threading.main_thread():
        raise StoreError('client-run-requires-main-thread')
    if (not isinstance(message,str) or not message.strip() or '\0' in message
            or len(message.encode()) > MAX_BYTES or type(timeout) is not int or not 1 <= timeout <= 900):
        raise StoreError('invalid-client-message')
    if _adapter is None:
        connection = normalize_connection(decode_document(read_private(directory/'connection.json')))
        probe_connection(connection,confirmed_endpoint=connection['baseUrl'])
    env = _render_environment(directory/'pixel-source',directory)
    # Installed runtimes may change independently of the prepared source/config.
    executable = Path(env['OPENCLAW_BIN'])
    if (not executable.is_absolute() or not executable.is_file()
            or not _command([str(executable),'--version']).decode().startswith('OpenClaw '+OPENCLAW_VERSION+' ')):
        raise StoreError('unsupported-openclaw-version')
    session_id = str(uuid.uuid4())
    run = directory/'runs'/session_id
    run.parent.mkdir(mode=0o700,exist_ok=True)
    info = run.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise StoreError('unsafe-client-directory')
    run.mkdir(mode=0o700)
    _write_private(run/'message.txt',message.encode())
    args = [env['OPENCLAW_BIN'],'agent','--local','--agent',record['agentId'],'--session-id',session_id,
        '--message-file',str(run/'message.txt'),'--timeout',str(timeout),'--json']
    with _adapter.activate(run,env,record['agentId']) if _adapter else nullcontext(env) as effective_env:
        if effective_env.get('PIXEL_MODEL_REASONING','').lower() in ('0','false','off','no'):
            args.extend(['--thinking','off'])
        output,error,exit_code,interrupted = _run_process(args,effective_env,directory,timeout,lock_fd=_lock_fd)
    _write_private(run/'output.json',output)
    _write_private(run/'stderr.txt',error)
    try:
        result = json.loads(output)
    except ValueError:
        result = None
    response = {'status':'interrupted' if interrupted else 'process-exited','exitCode':exit_code,
        'sessionId':session_id,'evidence':str(run),'result':result}
    if _adapter:
        response['providerRuntime'] = {'revision':_adapter.config['revision'],'status':_adapter.status}
    _write_private(run/'result.json',_json(response))
    return response


def _run_process(args,env,directory,timeout,*,lock_fd=None):
    child = None
    interrupted = False
    requested = False
    def interrupt(_number,_frame):
        nonlocal requested
        requested = True
        if child is not None:
            raise KeyboardInterrupt
    previous = {number:signal.getsignal(number) for number in (signal.SIGINT,signal.SIGTERM)}
    for number in previous:
        signal.signal(number,interrupt)
    try:
        try:
            # Keep admission held by the agent too if this supervisor crashes.
            child = subprocess.Popen(args,env=env,cwd=directory,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                start_new_session=True,pass_fds=() if lock_fd is None else (lock_fd,))
            if requested:
                raise KeyboardInterrupt
            output,error = child.communicate(timeout=timeout+15)
        except (subprocess.TimeoutExpired,KeyboardInterrupt):
            interrupted = True
            # Repeated interrupt signals must not interrupt bounded reaping.
            for number in previous:
                signal.signal(number,signal.SIG_IGN)
            try:
                os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                output,error = child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:
                    pass
                output,error = child.communicate()
    finally:
        for number,handler in previous.items():
            signal.signal(number,handler)
    return output,error,child.returncode,interrupted
