"""Root-issued recovery attestations; sharing the workspace UID grants no issuance."""
import json
import hashlib
import os
from pathlib import Path
import re
import stat
import secrets
import ctypes

ROOT = Path('/var/lib/ods-pixel-project-resolutions')
CONFIG = Path('/etc/ods-pixel-project.json')


def installation_identity(config, state_root):
    binding = {'ownerUid': config['ownerUid'], 'workspace': config['workspace'], 'stateRoot': state_root}
    return hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def installed_identity():
    fd = os.open(CONFIG, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022:
            raise PermissionError('root-owned installed configuration required')
        data = stream.read(16385)
    if len(data) > 16384:
        raise ValueError('configuration exceeds bound')
    return hashlib.sha256(data).hexdigest(), json.loads(data)


def _directory():
    fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if info.st_uid != 0 or info.st_mode & 0o022:
        os.close(fd)
        raise PermissionError('root-owned resolution directory required')
    return fd


def read_resolution(job, state_root, receipt_hash):
    if not re.fullmatch(r'ods-project-[a-f0-9]{24}', job) or not re.fullmatch(r'[a-f0-9]{64}', receipt_hash):
        return None
    try:
        parent = _directory()
        try:
            fd = os.open(job + '-' + receipt_hash + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022:
                    return None
                data = stream.read(16385)
                if len(data) > 16384:
                    return None
                value = json.loads(data)
                if not isinstance(value, dict):
                    return None
                record = json.loads(value['record'])
                _, config = installed_identity()
                if (record['ownerUid'] != os.geteuid() or record['ownerUid'] != config['ownerUid']
                        or record['stateRootSha256'] != hashlib.sha256(state_root.encode()).hexdigest()
                        or record['installationSha256'] != installation_identity(config, state_root)):
                    return None
                return value
        finally:
            os.close(parent)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_resolution(job, value):
    # Called with saved root UID but owner real/effective UID for inspection.
    # Ordinary owner/model processes cannot elevate here.
    if os.getresuid()[2] != 0 or not re.fullmatch(r'ods-project-[a-f0-9]{24}', job):
        raise PermissionError('protected administrator invocation required')
    receipt_hash = value.get('receipt_hash')
    if not isinstance(receipt_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', receipt_hash):
        raise ValueError('exact receipt binding required')
    owner = os.geteuid()
    try:
        os.seteuid(0)
        ROOT.mkdir(mode=0o755, exist_ok=True)
        parent = _directory()
        try:
            encoded = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
            if len(encoded) > 16384:
                raise ValueError('resolution exceeds bound')
            temporary = '.pending-' + secrets.token_hex(16)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Atomic publication without replacing an earlier attestation.
                rename = ctypes.CDLL(None, use_errno=True).renameat2
                rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
                rename.restype = ctypes.c_int
                if rename(parent, temporary.encode(), parent, (job + '-' + receipt_hash + '.json').encode(), 1) != 0:
                    error = ctypes.get_errno()
                    raise OSError(error, os.strerror(error))
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
                os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        os.seteuid(owner)
