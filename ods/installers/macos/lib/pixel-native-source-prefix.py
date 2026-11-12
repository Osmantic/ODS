#!/usr/bin/env python3
"""Preserve one explicitly selected empty, unrecorded first Source gate.

Administrator-only repair, outside the runtime protocol. The old inode is
archived, never adopted. No existing receipt, permissions or account is repaired,
and this operation grants no Source execution or Google authority.
"""
import argparse
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys

ARCHIVES = Path('/private/var/lib/ods-pixel-source-recovery')
COPIES = {'identity.json': 'source-identity.json', 'custody.json': 'source-custody-prepare.json',
          'code.json': 'source-installer-code-only.json'}
LATER = {'runtime-upgrade.json', 'transition.json', 'connection.json', 'source-environment.json',
         'source-logs-prepare.json', 'source-initial-generation.json', 'source-initial-boot.json',
         'source-jobs-prepare.json', 'source-jobs-active.json', 'source-execution.json',
         'source-environment-generation.json'}


class PrefixError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise PrefixError(code)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def owner_relay_key(settings, path):
    """The controller pins an owner-private credential; it is not a Root file."""
    owner = pwd.getpwnam(settings['owner'])
    expected = settings.get('edge_owner_key_sha256')
    require(owner.pw_uid > 0 and type(expected) is str and re.fullmatch('[a-f0-9]{64}', expected),
        'prefix-owner-credential-binding-required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == owner.pw_uid and before.st_nlink == 1
            and stat.S_IMODE(before.st_mode) == 0o600 and 32 <= before.st_size <= 4096,
            'prefix-owner-credential-unsafe')
        body = stream.read(4097)
        after = os.fstat(stream.fileno())
        def stamp(s):
            return (s.st_dev, s.st_ino, s.st_uid, s.st_gid, s.st_mode,
                s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(stamp(before) == stamp(after) and len(body) <= 4096
            and hashlib.sha256(body).hexdigest() == expected, 'prefix-owner-credential-changed')
    try:
        key = body.decode('ascii')
    except UnicodeError:
        raise PrefixError('prefix-owner-credential-invalid') from None
    require(re.fullmatch(r'[!-~]{32,4096}', key), 'prefix-owner-credential-invalid')
    return key


def inode(info):
    return [info.st_dev, info.st_ino]


def selection_id(expected):
    require(type(expected) is dict and set(expected) in ({'runtime', 'gate', 'images'},
        {'runtime', 'gate', 'images', 'child'}), 'prefix-selection-invalid')
    require(type(expected['runtime']) is str and re.fullmatch('[a-f0-9]{64}', expected['runtime']), 'prefix-selection-invalid')
    pair = expected['gate']
    require(type(pair) is list and len(pair) == 2 and all(type(n) is int and n > 0 for n in pair), 'prefix-selection-invalid')
    if 'child' in expected:
        child = expected['child']
        require(type(child) is dict and set(child) == {'name', 'inode'} and child['name'] == 'proposals',
            'prefix-selection-invalid')
        pair = child['inode']
        require(type(pair) is list and len(pair) == 2 and all(type(n) is int and n > 0 for n in pair),
            'prefix-selection-invalid')
    require(type(expected['images']) is dict and set(expected['images']) == set(COPIES), 'prefix-selection-invalid')
    for image in expected['images'].values():
        require(type(image) is dict and set(image) == {'inode', 'sha256'}, 'prefix-selection-invalid')
        pair = image['inode']
        require(type(pair) is list and len(pair) == 2 and all(type(n) is int and n > 0 for n in pair)
            and type(image['sha256']) is str and re.fullmatch('[a-f0-9]{64}', image['sha256']), 'prefix-selection-invalid')
    return hashlib.sha256(encoded(expected)).hexdigest()


def empty_gate(c, parent, name, expected):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = os.fstat(fd)
        require(inode(before) == expected and (before.st_uid, before.st_gid) == (0, 0)
            and stat.S_IMODE(before.st_mode) == 0o700, 'prefix-gate-changed')
        c._native_no_acl(fd)
        require(not os.listdir(fd), 'prefix-gate-not-empty')
        after = os.fstat(fd)
        def stamp(s):
            return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_mtime_ns, s.st_ctime_ns)
        require(stamp(before) == stamp(after) and c._native_at_inode(parent, name) == expected,
            'prefix-gate-changed')
    finally:
        os.close(fd)


def actors_absent(c, bridge, uid):
    raw = bridge.command(['/bin/launchctl', 'print', 'system'], timeout=10)
    require(raw.startswith('system = {') and raw.rstrip().endswith('}') and len(raw) <= 1024 * 1024
        and c.NATIVE_LABEL not in raw, 'prefix-source-job-present')
    rows = bridge.command(['/bin/ps', '-axo', 'pid=,uid='], timeout=10).splitlines()
    require(bool(rows), 'prefix-process-census-invalid')
    for row in rows:
        cells = row.split()
        require(len(cells) == 2 and all(cell.isdigit() for cell in cells), 'prefix-process-census-invalid')
        require(int(cells[1]) != uid, 'prefix-source-process-present')


def qualify(c, mac, selected, expected):
    """Fresh live authority, exact receipts, and no later Source lifecycle."""
    doc = c._native_selected(selected)
    require(doc['bundleSha256'] == expected['runtime'], 'prefix-runtime-changed')
    history = c.native_installer_code_only_history(selected)
    manifest, kit_sha = c.native_verify_kit(selected, require_profiles=True)
    require(manifest['schemaVersion'] == 2, 'prefix-kit-incomplete')
    bodies = {}
    with mac.protected_directory(c.NATIVE_META) as metadata:
        names = set(os.listdir(metadata))
        require(not names.intersection(LATER), 'prefix-later-lifecycle-present')
        allowed = {'source-identity.json', 'source-custody-prepare.json', 'source-runtime-installation.json',
            'source-runtime-publication.json', 'source-sidefiles.json', 'source-publication.json', *history['metadata']}
        require({name for name in names if name.startswith(('source-', '.source-'))} <= allowed,
            'prefix-source-metadata-unknown')
        for dest, source in COPIES.items():
            body, pair = c._native_root_file(metadata, source, limit=131072)
            require({'inode': pair, 'sha256': hashlib.sha256(body).hexdigest()} == expected['images'][dest],
                'prefix-receipt-changed')
            bodies[dest] = body
    identity = c._native_identity_intent(json.loads(bodies['identity.json'], object_pairs_hook=c._native_pairs))
    for kind in ('Users', 'Groups'):
        c._native_identity_record(c._native_ds_record(kind), c._native_identity_attributes(identity, kind), complete=True)
    c._native_identity_collisions(identity)
    lib = c._native_acl_library()
    import uuid
    require(c._native_uuid(lib, identity['id']) == uuid.UUID(identity['userGuid']).bytes, 'prefix-identity-changed')
    owner_guid = str(uuid.UUID(bytes=c._native_uuid(lib, doc['ownerUid']))).upper()
    custody = json.loads(bodies['custody.json'], object_pairs_hook=c._native_pairs)
    binding = c._native_prepare_binding(doc, identity, owner_guid, kit_sha, selected.deployment.checksum)
    c._native_validate_prepare_intent(custody, binding)
    if 'child' in expected:
        qualify_child(c, mac, doc, identity, custody, expected)
    else:
        require(custody['gate'] is None and custody['children'] == {}, 'prefix-gate-already-recorded')
    code = json.loads(bodies['code.json'], object_pairs_hook=c._native_pairs)
    require(code.get('phase') == 'committed' and code.get('deploymentSha256') == selected.deployment.checksum,
        'prefix-code-not-committed')
    require(not os.path.lexists(c.NATIVE_LOGS) and not os.path.lexists(c.NATIVE_CONNECTION_RECORD),
        'prefix-source-data-present')
    actors_absent(c, selected.bridge, identity['id'])
    return bodies


def qualify_child(c, mac, doc, identity, custody, expected):
    """Only the final empty allocation, before any environment/data authority."""
    layout = c._native_layout(doc, identity)
    require(custody['gate'] == expected['gate'] and set(custody['children']) ==
        {relative for relative, *_ in layout} - {'proposals'}, 'prefix-child-receipts-invalid')
    with mac.protected_directory(c.NATIVE_STATE.parent) as parent:
        gate = os.open(c.NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(gate)
            require(inode(info) == expected['gate'] and (info.st_uid, info.st_gid) == (0, 0)
                and stat.S_IMODE(info.st_mode) == 0o700, 'prefix-recorded-gate-changed')
            c._native_no_acl(gate)
            require(set(os.listdir(gate)) in ({'private', 'projection', 'results'},
                {'private', 'projection', 'results', 'proposals'}), 'prefix-child-data-present')
            for relative, uid, gid, mode in layout:
                if relative == 'proposals':
                    continue
                child = c._native_directory_fd(gate, relative)
                try:
                    info = os.fstat(child)
                    require(custody['children'][relative] == {'inode': inode(info), 'ready': True}
                        and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (uid, gid, mode),
                        'prefix-recorded-child-changed')
                    c._native_no_acl(child)
                    require(set(os.listdir(child)) == ({'oauth'} if relative == 'private' else set()),
                        'prefix-child-data-present')
                finally:
                    os.close(child)
            if c._native_at_inode(gate, 'proposals') is not None:
                empty_gate(c, gate, 'proposals', expected['child']['inode'])
            require(c._native_at_inode(parent, c.NATIVE_STATE.name) == expected['gate'],
                'prefix-recorded-gate-changed')
        finally:
            os.close(gate)


def private_dir(c, fd):
    info = os.fstat(fd)
    require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid) == (0, 0)
        and stat.S_IMODE(info.st_mode) == 0o700, 'prefix-archive-directory-unsafe')
    c._native_no_acl(fd)


@contextmanager
def source_gate(c, mac, expected):
    with mac.protected_directory(c.NATIVE_STATE.parent) as parent:
        fd = os.open(c.NATIVE_STATE.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            private_dir(c, fd)
            require(inode(os.fstat(fd)) == expected['gate'], 'prefix-recorded-gate-changed')
            yield fd
            require(c._native_at_inode(parent, c.NATIVE_STATE.name) == expected['gate'],
                'prefix-recorded-gate-changed')
            private_dir(c, fd)
        finally:
            os.close(fd)


def make_private_dir(c, parent, name):
    os.mkdir(name, mode=0o700, dir_fd=parent)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        # These are exclusively created inodes, never permission repairs.
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o700)
        private_dir(c, fd)
        os.fsync(fd)
        os.fsync(parent)
    finally:
        os.close(fd)


def write_new(c, directory, name, body):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, 'wb') as stream:
        os.fchown(stream.fileno(), 0, 0)
        os.fchmod(stream.fileno(), 0o600)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(directory)
    saved, pair = c._native_root_file(directory, name, limit=131072)
    require(saved == body, 'prefix-archive-write-unconfirmed')
    return pair


def rename_exclusive(source, name, target, destination):
    require(sys.platform == 'darwin', 'prefix-darwin-required')
    operation = ctypes.CDLL(None, use_errno=True).renameatx_np
    operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    operation.restype = ctypes.c_int
    if operation(source, os.fsencode(name), target, os.fsencode(destination), 0x4):
        raise OSError(ctypes.get_errno(), 'prefix-exclusive-rename-failed')


def capsule(c, directory, expected, tag):
    private_dir(c, directory)
    leaf = 'child' if 'child' in expected else 'gate'
    allocated = expected['child']['inode'] if 'child' in expected else expected['gate']
    names = set(os.listdir(directory))
    require(names in (set(COPIES) | {'manifest.json'}, set(COPIES) | {'manifest.json', leaf}),
        'prefix-archive-unsealed-or-unknown')
    body, _ = c._native_root_file(directory, 'manifest.json', limit=131072)
    value = json.loads(body, object_pairs_hook=c._native_pairs)
    require(type(value) is dict and set(value) == {'schemaVersion', 'kind', 'archiveId', 'expected', 'copies'}
        and type(value['schemaVersion']) is int and value['schemaVersion'] == 1
        and value['kind'] == 'ods-native-source-first-gate-preservation' and value['archiveId'] == tag
        and value['expected'] == expected and type(value['copies']) is dict
        and set(value['copies']) == set(COPIES), 'prefix-archive-manifest-invalid')
    for name in COPIES:
        raw, pair = c._native_root_file(directory, name, limit=131072)
        require(pair == value['copies'][name] and hashlib.sha256(raw).hexdigest() == expected['images'][name]['sha256'],
            'prefix-archive-copy-changed')
    if leaf in names:
        empty_gate(c, directory, leaf, allocated)
    return leaf in names


def preserve_empty_child(c, mac, selected, expected):
    """Retain the final unrecorded allocation; never adopt its inode."""
    c._native_root()
    tag = selection_id(expected)
    require('child' in expected, 'prefix-child-selection-required')
    bodies = qualify(c, mac, selected, expected)
    require(ARCHIVES.parent == c.NATIVE_STATE.parent, 'prefix-archive-path-invalid')
    with mac.protected_directory(ARCHIVES.parent) as parent:
        if c._native_at_inode(parent, ARCHIVES.name) is None:
            make_private_dir(c, parent, ARCHIVES.name)
    with mac.protected_directory(ARCHIVES) as root, source_gate(c, mac, expected) as gate:
        private_dir(c, root)
        require(inode(os.fstat(gate)) == expected['gate'], 'prefix-recorded-gate-changed')
        name = 'first-child-' + tag
        fresh = c._native_at_inode(root, name) is None
        if fresh:
            empty_gate(c, gate, 'proposals', expected['child']['inode'])
            make_private_dir(c, root, name)
        with mac.protected_directory(ARCHIVES / name) as archive:
            private_dir(c, archive)
            require(os.fstat(archive).st_dev == expected['child']['inode'][0], 'prefix-cross-device-preservation-refused')
            if fresh:
                pairs = {name: write_new(c, archive, name, body) for name, body in bodies.items()}
                require(qualify(c, mac, selected, expected) == bodies, 'prefix-receipts-changed')
                empty_gate(c, gate, 'proposals', expected['child']['inode'])
                write_new(c, archive, 'manifest.json', encoded({'schemaVersion': 1,
                    'kind': 'ods-native-source-first-gate-preservation', 'archiveId': tag,
                    'expected': expected, 'copies': pairs}))
            if capsule(c, archive, expected, tag):
                require(c._native_at_inode(gate, 'proposals') is None, 'prefix-child-slot-not-empty')
            else:
                require(qualify(c, mac, selected, expected) == bodies, 'prefix-receipts-changed')
                empty_gate(c, gate, 'proposals', expected['child']['inode'])
                rename_exclusive(gate, 'proposals', archive, 'child')
                os.fsync(gate)
                os.fsync(archive)
            require(capsule(c, archive, expected, tag) and c._native_at_inode(gate, 'proposals') is None,
                'prefix-preservation-unconfirmed')
    return {'status': 'preserved-empty-first-child', 'archiveId': tag, 'sourcePrepared': False,
            'archive': str(ARCHIVES / name)}


def preserve_empty_gate(c, mac, selected, expected):
    """Caller holds the existing Root lock and bounded, sealed runtime scope."""
    c._native_root()
    tag = selection_id(expected)
    # Every retry requalifies the original live authority and receipt images.
    bodies = qualify(c, mac, selected, expected)
    require(ARCHIVES.parent == c.NATIVE_STATE.parent, 'prefix-archive-path-invalid')
    with mac.protected_directory(c.NATIVE_STATE.parent) as parent:
        if c._native_at_inode(parent, ARCHIVES.name) is None:
            empty_gate(c, parent, c.NATIVE_STATE.name, expected['gate'])
            make_private_dir(c, parent, ARCHIVES.name)
        with mac.protected_directory(ARCHIVES) as root:
            private_dir(c, root)
            name = 'first-gate-' + tag
            fresh = c._native_at_inode(root, name) is None
            if fresh:
                empty_gate(c, parent, c.NATIVE_STATE.name, expected['gate'])
                make_private_dir(c, root, name)
            with mac.protected_directory(ARCHIVES / name) as archive:
                private_dir(c, archive)
                require(os.fstat(archive).st_dev == expected['gate'][0], 'prefix-cross-device-preservation-refused')
                if fresh:
                    pairs = {name: write_new(c, archive, name, body) for name, body in bodies.items()}
                    require(qualify(c, mac, selected, expected) == bodies, 'prefix-receipts-changed')
                    empty_gate(c, parent, c.NATIVE_STATE.name, expected['gate'])
                    write_new(c, archive, 'manifest.json', encoded({'schemaVersion': 1,
                        'kind': 'ods-native-source-first-gate-preservation', 'archiveId': tag,
                        'expected': expected, 'copies': pairs}))
                retained = capsule(c, archive, expected, tag)
                live = c._native_at_inode(parent, c.NATIVE_STATE.name)
                if retained:
                    require(live is None, 'prefix-source-slot-not-empty')
                else:
                    require(live == expected['gate'], 'prefix-source-gate-changed')
                    # Fresh checks immediately precede the one irreversible
                    # namespace change. It retains the old inode and all bytes.
                    require(qualify(c, mac, selected, expected) == bodies, 'prefix-receipts-changed')
                    empty_gate(c, parent, c.NATIVE_STATE.name, expected['gate'])
                    rename_exclusive(parent, c.NATIVE_STATE.name, archive, 'gate')
                    os.fsync(parent)
                    os.fsync(archive)
                require(capsule(c, archive, expected, tag) and c._native_at_inode(parent, c.NATIVE_STATE.name) is None,
                    'prefix-preservation-unconfirmed')
    return {'status': 'preserved-empty-first-gate', 'archiveId': tag, 'sourcePrepared': False,
            'archive': str(ARCHIVES / ('first-gate-' + tag))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--selection', required=True, help='Private JSON with the explicitly selected runtime, gate and receipt images')
    args = parser.parse_args()
    require(sys.platform == 'darwin' and os.geteuid() == 0, 'prefix-root-darwin-required')
    sys.path.insert(0, '/usr/local/libexec/ods-pixel-access')
    from pixel_access_bridge import LaunchdAccessBridge
    from pixel_macos_custody import protected_bytes
    import pixel_macos_custody as mac
    from pixel_connections import coordinator, custody as c, source_backend
    settings = json.loads(protected_bytes('/private/etc/ods/pixel-access.json'))
    require(args.install_dir == settings['install_dir'] and os.environ.get('SUDO_USER') == settings['owner'],
        'prefix-owner-or-install-mismatch')
    # Selection is a constraint, never a receipt or authority. Authority is
    # reconstructed from the installed Root bridge and live custody below.
    fd = os.open(args.selection, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 16384,
            'prefix-selection-invalid')
        raw = stream.read(16385)
    require(len(raw) <= 16384, 'prefix-selection-invalid')
    expected = json.loads(raw, object_pairs_hook=c._native_pairs)
    selection_id(expected)
    key = owner_relay_key(settings, '/private/etc/ods/pixel-access-relay.key')
    bridge = LaunchdAccessBridge(Path(settings['install_dir']), key,
        gateway_target=settings['gateway_target'], gateway_plist=settings['gateway_plist'],
        gateway_process=settings['gateway_process'], state=Path(settings['state_dir']),
        gateway_binding=settings['gateway_binding'], installed_binary=settings['openclaw_bin'],
        gateway_policy=settings['gateway_policy'], gateway_owner=settings['owner'],
        settings_data_dir=settings.get('settings_data_dir'), gateway_port=settings.get('gateway_port'))
    require(os.path.lexists(bridge.state / 'lock'), 'prefix-original-lock-missing')
    with bridge.bounded(180), bridge.locked(wait_seconds=30):
        bridge.discover()
        coordinator._guard_native_enrollment(bridge)
        coordinator._guard_google_exchange(bridge)
        coordinator._guard_fresh_source(bridge)
        require(bridge.pending() is None, 'prefix-transition-pending')
        selected = source_backend.for_bridge(bridge, linux_paths=coordinator.PATHS, linux_run=coordinator.RUN).selection
        operation = preserve_empty_child if 'child' in expected else preserve_empty_gate
        print(json.dumps(operation(c, mac, selected, expected)), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        code = getattr(error, 'code', str(error))
        code = code if type(code) is str and re.fullmatch('[a-z0-9-]{1,120}', code) else 'prefix-preservation-failed'
        print(json.dumps({'status': 'stopped', 'error': code, 'type': type(error).__name__,
            'errno': error.errno if isinstance(error, OSError) else None}), flush=True)
        sys.exit(1)
