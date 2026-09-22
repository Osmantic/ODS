"""Deterministic Seatbelt filesystem policies for native Pixel qualification.

These policies do not establish runtime custody, launchd process identity or
Linux NoNewPrivileges equivalence. The installer must qualify those separately.
"""
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import tempfile


class PolicyError(ValueError):
    pass


MODES = ('sandboxed', 'full-access')


def deployment_receipt(directory, policies):
    """Pin installer-rendered profiles; the receipt itself needs root custody."""
    directory = Path(_path(directory))
    if (type(policies) is not dict or set(policies) != set(MODES)
            or any(type(body) is not bytes or not body or len(body) > 1024 * 1024
                   for body in policies.values())):
        raise PolicyError('invalid-policy-deployment')
    receipt = {'schemaVersion': 1, 'active': str(directory / 'pixel-gateway.sb'),
               'profiles': {mode: {
                   'path': str(directory / ('pixel-gateway.' + mode + '.sb')),
                   'sha256': hashlib.sha256(policies[mode]).hexdigest(),
               } for mode in MODES}}
    return validate_receipt(receipt)


def validate_receipt(receipt):
    if (type(receipt) is not dict or set(receipt) != {'schemaVersion', 'active', 'profiles'}
            or type(receipt['schemaVersion']) is not int or receipt['schemaVersion'] != 1
            or type(receipt['profiles']) is not dict or set(receipt['profiles']) != set(MODES)):
        raise PolicyError('invalid-policy-receipt')
    active = Path(_path(receipt['active']))
    if active.name != 'pixel-gateway.sb':
        raise PolicyError('invalid-policy-receipt')
    profiles = {}
    for mode in MODES:
        record = receipt['profiles'][mode]
        if (type(record) is not dict or set(record) != {'path', 'sha256'}
                or _path(record['path']) != str(active.with_name('pixel-gateway.' + mode + '.sb'))
                or type(record['sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', record['sha256'])):
            raise PolicyError('invalid-policy-receipt')
        profiles[mode] = dict(record)
    if profiles[MODES[0]]['sha256'] == profiles[MODES[1]]['sha256']:
        raise PolicyError('identical-access-policies')
    return {'schemaVersion': 1, 'active': str(active), 'profiles': profiles}


def _deployment(receipt):
    from pixel_macos_custody import CustodyError, protected_bytes
    receipt = validate_receipt(receipt)
    try:
        policies = {}
        for mode, record in receipt['profiles'].items():
            body = protected_bytes(record['path'])
            if hashlib.sha256(body).hexdigest() != record['sha256']:
                raise PolicyError('approved-policy-changed')
            policies[mode] = body
        active = protected_bytes(receipt['active'])
    except CustodyError:
        raise PolicyError('policy-custody-required') from None
    modes = [mode for mode, body in policies.items() if body == active]
    if len(modes) != 1:
        raise PolicyError('unapproved-active-policy')
    return receipt, policies, modes[0]


def policy_state(receipt):
    """Verify on-disk deployment, not the profile loaded by a running process."""
    receipt, _, mode = _deployment(receipt)
    return dict(receipt, activeMode=mode)


def select_policy(receipt, mode):
    """Atomically select approved bytes under the coordinator transaction lock.

    Ancestors and files must already have root custody, including absence of
    ACL grants. No missing or drifted file is repaired. Existing processes keep
    their old Seatbelt profile until the caller restarts and probes them.
    """
    if mode not in MODES:
        raise PolicyError('invalid-access-mode')
    if os.geteuid() != 0:
        raise PolicyError('root-policy-controller-required')
    receipt, policies, previous = _deployment(receipt)
    if previous == mode:
        return
    active = Path(receipt['active'])
    fd, temporary = tempfile.mkstemp(prefix='.ods-policy-', dir=active.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            os.fchmod(handle.fileno(), 0o644)
            os.fchown(handle.fileno(), 0, 0)
            handle.write(policies[mode])
            handle.flush()
            os.fsync(handle.fileno())
        # Reject concurrent deployment changes before publishing. The parent
        # was root-verified above; unprivileged processes cannot replace it.
        if _deployment(receipt)[2] != previous:
            raise PolicyError('policy-selection-changed')
        os.replace(temporary, active)
        parent = os.open(active.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        if policy_state(receipt)['activeMode'] != mode:
            raise PolicyError('policy-selection-changed')
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _path(value):
    value = str(value) if isinstance(value, PurePosixPath) else value
    if (not isinstance(value, str) or not value.startswith('/') or value == '/'
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in ('', '.', '..') for part in value.split('/')[1:])):
        raise PolicyError('invalid-policy-path')
    return value


def _quoted(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _paths(values):
    if type(values) not in (list, tuple) or not values:
        raise PolicyError('policy-paths-required')
    return tuple(sorted(set(_path(value) for value in values)))


def render_policy(*, mode, writable, protected, readable=(), sockets=(), probe):
    """Only filesystem reach changes with mode; no allow-default escape.

    Inputs are installer-selected canonical paths, never chat/request values.
    Root-owned deployment receipts must pin the rendered bytes before use.
    """
    if mode not in ('sandboxed', 'full-access'):
        raise PolicyError('invalid-access-mode')
    writable, protected = _paths(writable), _paths(protected)
    readable = _paths(readable) if readable else ()
    sockets = _paths(sockets) if sockets else ()
    probe = _path(probe)
    for mutable in (*writable, probe):
        for fixed in protected:
            if PurePosixPath(fixed) == PurePosixPath(mutable) or PurePosixPath(fixed) in PurePosixPath(mutable).parents:
                raise PolicyError('mutable-path-inside-protected-runtime')
    lines = ['(version 1)', '(deny default)', '(import "system.sb")',
             '(allow process-exec process-fork signal sysctl-read)',
             '(allow network*)', '(allow file-read-metadata)']
    read_paths = sorted(set(('/System', '/usr', '/bin', '/sbin', '/private/etc',
                             *readable, *protected)))
    if mode == 'full-access':
        lines.append('(allow file-read* file-write*)')
    else:
        lines.append('(allow file-read* ' + ' '.join('(subpath ' + _quoted(p) + ')' for p in read_paths) + ')')
        lines.append('(allow file-read* file-write* ' + ' '.join(
            '(subpath ' + _quoted(p) + ')' for p in writable) + ')')
        # The gateway may inspect the owner-only fixture; sandboxed core tools
        # must still be unable to write outside their Docker workspace.
        lines.append('(allow file-read* (subpath ' + _quoted(probe) + '))')
    devices = ('/dev/null', '/dev/urandom', '/dev/random')
    lines.append('(allow file-read* file-write* ' + ' '.join(
        '(literal ' + _quoted(p) + ')' for p in (*devices, *sockets)) + ')')
    lines.append('(deny file-write* ' + ' '.join(
        '(subpath ' + _quoted(p) + ')' for p in protected) + ')')
    # Replacing an ancestor can otherwise substitute an entirely new runtime
    # without issuing a write against the old protected files themselves.
    ancestors = sorted({str(parent) for p in protected for parent in PurePosixPath(p).parents
                        if str(parent) != '/'})
    lines.append('(deny file-write-unlink ' + ' '.join(
        '(literal ' + _quoted(p) + ')' for p in ancestors) + ')')
    lines.append('(deny file-write-setugid file-write-mount file-write-umount)')
    return ('\n'.join(lines) + '\n').encode('utf-8')
