#!/usr/bin/env python3
"""Check a fresh ODS coordinator proof for this verifier's exact gateway."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

CLIENT = Path('/usr/local/libexec/ods-pixel-access/pixel_model_transition.py')
HEX = re.compile(r'[a-f0-9]{64}\Z')


def protected_client(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or (item == path and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
            raise ValueError('ODS verification client custody unavailable')


def validate_proof(value, config_path, pid):
    if (type(value) is not dict or set(value) != {'mode', 'pid', 'config_sha256'}
            or value.get('mode') not in ('sandboxed', 'full-access')
            or type(value.get('pid')) is not int or value['pid'] != pid or pid <= 0
            or type(value.get('config_sha256')) is not str
            or not HEX.fullmatch(value['config_sha256'])):
        raise ValueError('ODS gateway proof does not match the verifier')
    fd = os.open(config_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024:
            raise ValueError('ODS gateway configuration unavailable')
        raw = handle.read(16 * 1024 * 1024 + 1)
    if hashlib.sha256(raw).hexdigest() != value['config_sha256']:
        raise ValueError('ODS gateway configuration changed after proof')
    return value['mode']


def main(argv):
    if len(argv) != 3 or not HEX.fullmatch(argv[0]) or not argv[2].isdigit():
        raise ValueError('invalid ODS verification request')
    protected_client(CLIENT)
    # Isolated Python plus protected client/dependency custody prevents a
    # checkout module from fabricating the coordinator's response. The client
    # also checks that the connected Unix socket peer is root.
    raw = subprocess.check_output([sys.executable, '-I', str(CLIENT), 'verify',
                                   '--transaction', argv[0]], timeout=340)
    if len(raw) > 4096:
        raise ValueError('invalid ODS verification response')
    print(validate_proof(json.loads(raw), argv[1], int(argv[2])))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (OSError, ValueError, subprocess.SubprocessError):
        print('ODS managed access verification failed', file=sys.stderr)
        raise SystemExit(1) from None
