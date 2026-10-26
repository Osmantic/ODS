"""Durable, transaction-bound bootstrap inputs. Snapshots are evidence, not authority.

No automatic recovery picks a directory by age or by filename. The root
coordinator binds this exact manifest before holding admission. Recovery of
older transactions deliberately never imports an unbound snapshot.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile

HEX = re.compile(r'[a-f0-9]{64}\Z')
FILES = {'.env': 'env', 'config/llama-server/models.ini': 'config-llama-server/models.ini',
         'config/litellm/local.yaml': 'litellm-local.yaml'}


def target(value):
    if (type(value) is not dict or set(value) != {'gguf', 'model', 'context'}
            or type(value['gguf']) is not str or not 1 <= len(value['gguf']) <= 255
            or any(c in value['gguf'] for c in '/\\')
            or any(ord(c) < 32 or ord(c) == 127 for c in value['gguf'])
            or not value['gguf'].endswith('.gguf')
            or type(value['model']) is not str
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{0,255}', value['model'])
            or type(value['context']) is not int or not 4096 <= value['context'] <= 10000000):
        raise ValueError('invalid-bootstrap-target')
    return value


def read(path, uid):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid
                or info.st_mode & 0o022 or info.st_size > 4 * 1024 * 1024):
            raise ValueError('unsafe-bootstrap-file')
        return stream.read(4 * 1024 * 1024 + 1)


def write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def directory(install, transaction):
    if type(transaction) is not str or not HEX.fullmatch(transaction):
        raise ValueError('invalid-bootstrap-transaction')
    return Path(install) / 'data' / ('bootstrap-upgrade-active-config.' + transaction)


def prepare(install, desired):
    install = Path(install).resolve(strict=True)
    desired = target(desired)
    transaction = secrets.token_hex(32)
    folder = directory(install, transaction)
    folder.mkdir(mode=0o700)
    parent_fd = os.open(folder.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(parent_fd)
    finally: os.close(parent_fd)
    before = {}
    for name, saved in FILES.items():
        try: body = read(install / name, os.geteuid())
        except FileNotFoundError: body = None
        before[name] = hashlib.sha256(body).hexdigest() if body is not None else None
        if body is not None: write(folder / saved, body)
        else: write(folder / (saved + '.missing'), b'')
    manifest = dict(version=1, provenance='bootstrap-upgrade', transactionId=transaction,
                    install=str(install), target=desired, before=before)
    write(folder / 'manifest.json', json.dumps(manifest, sort_keys=True).encode() + b'\n')
    validate(install, transaction, os.geteuid(), current=True)
    return transaction


def validate(install, transaction, uid, *, current=False, expected=None):
    install = Path(install).resolve(strict=True)
    folder = directory(install, transaction)
    # Do not traverse a replaced data directory or snapshot directory.
    for path in (install / 'data', folder, folder / 'config-llama-server'):
        if not path.exists(): continue
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
            raise ValueError('unsafe-bootstrap-snapshot')
    raw = read(folder / 'manifest.json', uid)
    digest = hashlib.sha256(raw).hexdigest()
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {'version', 'provenance', 'transactionId', 'install', 'target', 'before'}
            or value['version'] != 1 or value['provenance'] != 'bootstrap-upgrade'
            or value['transactionId'] != transaction or value['install'] != str(install)
            or type(value['before']) is not dict or set(value['before']) != set(FILES)
            or expected is not None and digest != expected):
        raise ValueError('bootstrap-snapshot-mismatch')
    target(value['target'])
    for name, saved in FILES.items():
        sha = value['before'][name]
        if sha is None:
            if read(folder / (saved + '.missing'), uid) != b'' or (folder / saved).exists():
                raise ValueError('bootstrap-snapshot-mismatch')
        elif type(sha) is not str or not HEX.fullmatch(sha) or hashlib.sha256(read(folder / saved, uid)).hexdigest() != sha:
            raise ValueError('bootstrap-snapshot-mismatch')
        if current:
            try: observed = hashlib.sha256(read(install / name, uid)).hexdigest()
            except FileNotFoundError: observed = None
            if observed != sha: raise ValueError('bootstrap-original-state-changed')
    return dict(provenance='bootstrap-upgrade', transactionId=transaction,
                snapshotSha256=digest, target=value['target'])


if __name__ == '__main__':
    if len(sys.argv) == 6 and sys.argv[1] == 'prepare':
        print(prepare(sys.argv[2], dict(gguf=sys.argv[3], model=sys.argv[4], context=int(sys.argv[5]))))
    elif len(sys.argv) == 4 and sys.argv[1] == 'verify':
        from pixel_model_transition import execute
        from pixel_access_client import request_access
        status = execute('status', request=request_access)
        intent = status.get('bootstrap', {})
        if not status['pending'] or status.get('transaction_id') != sys.argv[3] or intent.get('transactionId') != sys.argv[3]:
            raise SystemExit('bootstrap-snapshot-not-bound')
        validate(sys.argv[2], sys.argv[3], os.geteuid(), expected=intent['snapshotSha256'])
    else:
        raise SystemExit('usage: prepare INSTALL GGUF MODEL CONTEXT | verify INSTALL TRANSACTION')
