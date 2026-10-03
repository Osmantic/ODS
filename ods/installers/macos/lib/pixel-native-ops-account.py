"""Provision the dedicated native Operations identity with a root-owned intent.

Replays fill only missing attributes on the exact UUID-bound records. Existing
foreign records and changed attributes are never adopted or repaired silently.
"""
import argparse
import fcntl
import grp
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import subprocess
import stat
import sys
import uuid


SPEC = importlib.util.spec_from_file_location('ops_identity_custody',
    Path(__file__).resolve().parents[3] / 'bin/pixel_macos_custody.py')
custody = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(custody)
NAME = '_ods_pixel_ops'
HOME = '/private/var/lib/pixel-ops-broker'
ROOT = Path('/private/var/lib/ods-pixel-access')
PREFIX = 'dsAttrTypeStandard:'
SYSTEM_JOBS = ('com.ods.pixel-native-gateway', 'com.ods.pixel-access',
    'com.ods.pixel-access-relay', 'com.ods.pixel-native-manager',
    'com.ods.pixel-native-promoter', 'com.ods.pixel-native-operations')


def dscl(*arguments):
    return subprocess.run(['/usr/bin/dscl', '-plist', '.', *arguments],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=20)


def read_record(kind):
    result = dscl('-read', '/' + kind + '/' + NAME)
    if result.returncode:
        if b'eDSRecordNotFound' in result.stderr + result.stdout:
            return None
        raise ValueError('operations-directory-read-failed')
    value = plistlib.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError('operations-directory-record-invalid')
    result = {key.removeprefix(PREFIX): item for key, item in value.items()}
    # IsHidden is a native DirectoryService attribute, unlike UID and shell.
    if 'dsAttrTypeNative:IsHidden' in result:
        if 'IsHidden' in result:
            raise ValueError('operations-directory-record-invalid')
        result['IsHidden'] = result.pop('dsAttrTypeNative:IsHidden')
    return result


def create_attribute(kind, key, value):
    if dscl('-create', '/' + kind + '/' + NAME, key, value).returncode:
        raise ValueError('operations-directory-write-failed')


def validate_intent(value):
    if (type(value) is not dict or set(value) != {'schema', 'name', 'id', 'userGuid', 'groupGuid'}
            or type(value['schema']) is not int or value['schema'] != 1 or value['name'] != NAME
            or type(value['id']) is not int or not 60000 <= value['id'] < 65000):
        raise ValueError('operations-identity-intent-invalid')
    for key in ('userGuid', 'groupGuid'):
        if not isinstance(value[key], str) or str(uuid.UUID(value[key])).upper() != value[key]:
            raise ValueError('operations-identity-intent-invalid')
    if value['userGuid'] == value['groupGuid']:
        raise ValueError('operations-identity-intent-invalid')
    return value


def available_id():
    occupied = {p.pw_uid for p in pwd.getpwall()} | {g.gr_gid for g in grp.getgrall()}
    for value in range(60000, 65000):
        if value not in occupied:
            return value
    raise ValueError('operations-identity-id-exhausted')


def expected_attributes(intent, kind):
    if kind == 'Groups':
        return {'GeneratedUID': intent['groupGuid'], 'Password': '*',
                'PrimaryGroupID': str(intent['id'])}
    # UniqueID is deliberately last: incomplete accounts have no usable UID.
    return {'GeneratedUID': intent['userGuid'], 'Password': '*', 'UserShell': '/usr/bin/false',
            'NFSHomeDirectory': HOME, 'IsHidden': '1', 'PrimaryGroupID': str(intent['id']),
            'UniqueID': str(intent['id'])}


def verify_record(record, expected, *, complete=False):
    if record is None:
        if complete: raise ValueError('operations-identity-record-missing')
        return
    if record.get('GeneratedUID') != [expected['GeneratedUID']]:
        raise ValueError('operations-identity-foreign-record')
    for key, value in expected.items():
        if (key in record or complete) and record.get(key) != [value]:
            raise ValueError('operations-identity-attribute-conflict')
    for key in ('GroupMembership', 'GroupMembers', 'NestedGroups', 'AuthenticationAuthority', 'ShadowHashData'):
        if record.get(key):
            raise ValueError('operations-identity-authority-conflict')


def reconcile(intent):
    intent = validate_intent(intent)
    records = {kind: read_record(kind) for kind in ('Groups', 'Users')}
    for kind, record in records.items():
        verify_record(record, expected_attributes(intent, kind))
    for entry in pwd.getpwall():
        if entry.pw_uid == intent['id'] and entry.pw_name != NAME:
            raise ValueError('operations-identity-id-collision')
    for entry in grp.getgrall():
        if entry.gr_gid == intent['id'] and entry.gr_name != NAME:
            raise ValueError('operations-identity-id-collision')
        if NAME in entry.gr_mem:
            raise ValueError('operations-identity-unexpected-membership')
    for kind, record in records.items():
        expected = expected_attributes(intent, kind)
        for key, value in expected.items():
            if record is None or key not in record:
                create_attribute(kind, key, value)
        verify_record(read_record(kind), expected, complete=True)
    return {'name': NAME, 'uid': intent['id'], 'gid': intent['id'], 'home': HOME}


def provision():
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    with custody.protected_directory(ROOT, create=True) as directory:
        lock = os.open('ops-identity.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            custody._verify_fd(lock, directory=False)
            fcntl.flock(lock, fcntl.LOCK_EX)
            receipt = ROOT / 'ops-identity.json'
            if os.path.lexists(receipt):
                intent = validate_intent(json.loads(custody.protected_bytes(receipt, limit=4096)))
            else:
                if any(read_record(kind) is not None for kind in ('Users', 'Groups')):
                    raise ValueError('operations-identity-unmanaged-record')
                intent = {'schema': 1, 'name': NAME, 'id': available_id(),
                          'userGuid': str(uuid.uuid4()).upper(), 'groupGuid': str(uuid.uuid4()).upper()}
                temporary = '.ops-identity-' + uuid.uuid4().hex
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write((json.dumps(intent, sort_keys=True) + '\n').encode())
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.rename(temporary, 'ops-identity.json', src_dir_fd=directory, dst_dir_fd=directory)
                finally:
                    try: os.unlink(temporary, dir_fd=directory)
                    except FileNotFoundError: pass
                os.fsync(directory)
            return reconcile(intent)
        finally:
            os.close(lock)


def verify_identity_only():
    """Read-only proof that a retained service account is the *only* Pixel state.

    This permits a deliberately cleaned test host to reinstall without adopting
    an arbitrary account or silently resuming a partial/active installation.
    """
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    with custody.protected_directory(ROOT) as directory:
        if (stat.S_IMODE(os.fstat(directory).st_mode) != 0o700
                or set(os.listdir(directory)) != {'ops-identity.json', 'ops-identity.lock'}):
            raise ValueError('operations-identity-not-only-protected-state')
        lock = os.open('ops-identity.lock', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            custody._verify_fd(lock, directory=False)
            if stat.S_IMODE(os.fstat(lock).st_mode) != 0o600:
                raise ValueError('operations-identity-lock-custody-invalid')
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            receipt = os.open('ops-identity.json', os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory)
            try:
                custody._verify_fd(receipt, directory=False)
                if stat.S_IMODE(os.fstat(receipt).st_mode) != 0o600:
                    raise ValueError('operations-identity-receipt-custody-invalid')
            finally:
                os.close(receipt)
            intent = validate_intent(json.loads(custody.protected_bytes(
                ROOT / 'ops-identity.json', limit=4096)))
            for kind in ('Groups', 'Users'):
                verify_record(read_record(kind), expected_attributes(intent, kind), complete=True)
            user, group = pwd.getpwnam(NAME), grp.getgrnam(NAME)
            if (user.pw_uid != intent['id'] or user.pw_gid != intent['id']
                    or group.gr_gid != intent['id']):
                raise ValueError('operations-identity-id-conflict')
            for entry in pwd.getpwall():
                if entry.pw_uid == intent['id'] and entry.pw_name != NAME:
                    raise ValueError('operations-identity-id-collision')
            for entry in grp.getgrall():
                if ((entry.gr_gid == intent['id'] and entry.gr_name != NAME)
                        or NAME in entry.gr_mem):
                    raise ValueError('operations-identity-unexpected-membership')
            processes = subprocess.run(['/bin/ps', '-axo', 'uid='], capture_output=True,
                text=True, timeout=15, check=True)
            if str(intent['id']) in processes.stdout.split():
                raise ValueError('operations-identity-process-active')
            for label in SYSTEM_JOBS:
                job = subprocess.run(['/bin/launchctl', 'print', 'system/' + label],
                    capture_output=True, timeout=15, check=False)
                if job.returncode != 113:
                    raise ValueError('operations-identity-service-loaded-or-ambiguous')
            return {'name': NAME, 'uid': intent['id'], 'gid': intent['id']}
        finally:
            os.close(lock)


def verify_empty_home_only():
    """Prove the sole retained Operations home has no prior workload state."""
    identity = verify_identity_only()
    fd = os.open(HOME, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != identity['uid']
                or info.st_gid != identity['gid']
                or stat.S_IMODE(info.st_mode) != 0o750 or os.listdir(fd)):
            raise ValueError('operations-home-not-empty-or-owned')
    finally:
        os.close(fd)
    return identity


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-identity-only', action='store_true')
    parser.add_argument('--verify-empty-home-only', action='store_true')
    args = parser.parse_args(argv)
    if args.verify_identity_only and args.verify_empty_home_only:
        parser.error('select one verification mode')
    if args.verify_empty_home_only or args.verify_identity_only:
        try:
            result = verify_empty_home_only() if args.verify_empty_home_only else verify_identity_only()
        except (ValueError, OSError, subprocess.SubprocessError):
            # Distinguish an executed proof rejecting state from sudo failing to
            # run the proof. Do not expose private receipt contents in diagnostics.
            print('Native Pixel Operations identity verification rejected.', file=sys.stderr)
            return os.EX_DATAERR
    else:
        result = provision()
    print(json.dumps(result,
        sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
