#!/usr/bin/env python3
"""Use the installed root-authenticated client for an ODS release transaction."""
import json
from pathlib import Path
import re
import stat
import subprocess
import sys

CLIENT = Path('/usr/local/libexec/ods-pixel-access/pixel_model_transition.py')
HEX = re.compile(r'[a-f0-9]{64}\Z')


def invoke(action, transaction, value, outcome=None):
    if not HEX.fullmatch(transaction):
        raise ValueError('invalid release transaction')
    if action == 'prepare':
        if (outcome is None or not HEX.fullmatch(outcome) or not value.startswith('/')
                or any(c in value for c in '\x00\n\r\t')
                or any(p in ('', '.', '..') for p in value.split('/')[1:])):
            raise ValueError('invalid reviewed candidate')
        args = ['release-prepare', '--transaction', transaction,
                '--candidate', value, '--sha256', outcome]
        keys = {'beforeSha', 'afterSha'}
    elif action == 'publish' and value in ('apply', 'rollback') and outcome is None:
        args = ['release-publish', '--transaction', transaction, value]
        keys = {'configSha256'}
    elif action == 'finish' and HEX.fullmatch(value) and outcome in ('apply', 'rollback'):
        args = ['release-finish', '--transaction', transaction, '--sha256', value, outcome]
        keys = {'configSha256'}
    else:
        raise ValueError('invalid release operation')
    for item in (CLIENT, *CLIENT.parents):
        info = item.lstat()
        if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or (item == CLIENT and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
            raise ValueError('release client custody unavailable')
    raw = subprocess.check_output([sys.executable, '-I', str(CLIENT), *args], timeout=340)
    if len(raw) > 4096:
        raise ValueError('invalid release response')
    body = json.loads(raw)
    if (type(body) is not dict or set(body) != keys
            or any(type(body[key]) is not str or not HEX.fullmatch(body[key]) for key in keys)):
        raise ValueError('invalid release response')
    if action == 'finish' and body['configSha256'] != value:
        raise ValueError('release proof changed')
    return body


def main(argv):
    if len(argv) not in (3, 4):
        raise ValueError('invalid release request')
    body = invoke(*argv)
    # Fixed ordering allows Bash to read the hashes without evaluating code.
    print(' '.join(body[key] for key in
                   (('beforeSha', 'afterSha') if argv[0] == 'prepare' else ('configSha256',))))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except (OSError, ValueError, subprocess.SubprocessError):
        print('ODS managed release operation failed', file=sys.stderr)
        raise SystemExit(1) from None
