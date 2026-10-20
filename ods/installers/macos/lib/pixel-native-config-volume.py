"""Retain producer custody before creating immutable native config storage.

Rollback keeps this volume and its snapshots. Only the uninstall volume guard
may consume a completed producer receipt; an interrupted create needs review.
"""
import json
import os
from pathlib import Path
import re
import secrets
import stat

KEY = 'pixel-native-config'
LABEL = 'org.osmantic.ods.native-config-creation'
RECORD = 'data/pixel-native/preparation/ingress-config-volume.json'


def fingerprint(row):
    return {key: row.get(key) for key in ('Name', 'Labels', 'CreatedAt', 'Driver')}


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or before.st_size > 16384):
            raise ValueError('native-config-volume-receipt-custody-required')
        body = stream.read(16385)
        after = os.fstat(stream.fileno())
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(body) > 16384):
        raise ValueError('native-config-volume-receipt-changed')
    return body


def _write(path, value, previous):
    body = (json.dumps(value, sort_keys=True) + '\n').encode()
    # Exclusive first publication serializes even callers without an existing
    # refresh journal. A partial first write is pending evidence, never replayed.
    temporary = path if previous is None else path.with_name('.ingress-config-volume-' + secrets.token_hex(16))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    if previous is not None:
        if _read(path) != previous:
            raise ValueError('native-config-volume-receipt-changed')
        os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return body


def selection(document):
    project = document.get('name', '')
    name = project + '_' + KEY
    definition = document.get('volumes', {}).get(KEY)
    if (not re.fullmatch('[a-z0-9][a-z0-9_-]*', project) or type(definition) is not dict
            or set(definition) - {'name', 'driver', 'external', 'labels'}
            or definition.get('name', name) != name or definition.get('external', False) is not False
            or definition.get('driver', 'local') != 'local' or definition.get('labels', {}) != {}
            or any(key != KEY and value.get('name') == name for key, value in document['volumes'].items())):
        raise ValueError('native-config-volume-standard-mapping-required')
    for service, target, readonly in (('pixel-native-config-init', '/config', False),
            ('pixel-native-config-write', '/config', False), ('pixel-native-ingress', '/run/pixel-config', True)):
        mounts = document['services'][service].get('volumes', [])
        selected = [value for value in mounts if value.get('target') == target or value.get('source') == KEY]
        if (len(selected) != 1 or selected[0].get('type') != 'volume' or selected[0].get('source') != KEY
                or selected[0].get('target') != target or selected[0].get('read_only', False) is not readonly
                or set(selected[0]) - {'type', 'source', 'target', 'read_only', 'volume'}
                or selected[0].get('volume', {}) not in ({}, {'nocopy': True})):
            raise ValueError('native-config-volume-standard-mapping-required')
    return project, name


def ensure(docker_run, document, install_dir):
    project, name = selection(document)
    root = Path(install_dir)
    path = root / RECORD
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise ValueError('native-config-volume-install-root-required')
    for parent in (root, root / 'data', root / 'data/pixel-native', path.parent):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('native-config-volume-receipt-custody-required')
    previous = _read(path) if os.path.lexists(path) else None
    record = json.loads(previous) if previous is not None else None
    if record is not None and (type(record) is not dict or record.get('schemaVersion') != 1
            or set(record) != {'schemaVersion', 'installDir', 'project', 'key', 'state', 'nonce', 'preexisting', 'fingerprint'}
            or record.get('installDir') != str(root) or record.get('project') != project
            or record.get('key') != KEY or record.get('state') != 'ready'
            or record.get('preexisting') is not False
            or not re.fullmatch('[a-f0-9]{32}', record.get('nonce', ''))):
        raise ValueError('native-config-volume-creation-requires-review')

    def command(*args):
        result = docker_run(*args, timeout=30)
        if result.returncode or len(result.stdout) > 1024 * 1024:
            raise ValueError('native-config-volume-inspection-failed')
        return result.stdout

    names = command('volume', 'ls', '--quiet').split()
    if name not in names:
        if record is not None:
            raise ValueError('native-config-volume-identity-changed')
        record = {'schemaVersion': 1, 'installDir': str(root), 'project': project, 'key': KEY,
            'state': 'creating', 'nonce': secrets.token_hex(16), 'preexisting': False}
        previous = _write(path, record, None)
        # Docker create can return an existing volume. The persisted nonce must
        # also be present in the inspected immutable labels before claiming it.
        command('volume', 'create', '--driver', 'local', '--label', 'com.docker.compose.project=' + project,
            '--label', 'com.docker.compose.volume=' + KEY, '--label', LABEL + '=' + record['nonce'], name)
    inspected = json.loads(command('volume', 'inspect', name))
    if type(inspected) is not list or len(inspected) != 1 or type(inspected[0]) is not dict:
        raise ValueError('native-config-volume-inspection-failed')
    row = inspected[0]
    labels = row.get('Labels') or {}
    if (row.get('Name') != name or row.get('Driver') != 'local' or row.get('Scope') != 'local'
            or row.get('Options') not in (None, {}) or labels.get('com.docker.compose.project') != project
            or labels.get('com.docker.compose.volume') != KEY):
        raise ValueError('native-config-volume-identity-changed')
    if record is None and LABEL in labels:
        raise ValueError('native-config-volume-producer-receipt-required')
    consumers = command('ps', '-aq', '--no-trunc', '--filter', 'volume=' + name).split()
    if consumers:
        if len(consumers) > 64 or any(not re.fullmatch('[a-f0-9]{64}', value) for value in consumers):
            raise ValueError('native-config-volume-consumers-unverified')
        rows = json.loads(command('inspect', *consumers))
        if {value.get('Id') for value in rows} != set(consumers) or len(rows) != len(consumers):
            raise ValueError('native-config-volume-consumers-unverified')
        for value in rows:
            owner = value.get('Config', {}).get('Labels') or {}
            if (owner.get('com.docker.compose.project') != project
                    or owner.get('com.docker.compose.project.working_dir') != str(root)
                    or owner.get('com.docker.compose.service') not in
                        ('pixel-native-ingress', 'pixel-native-config-init', 'pixel-native-config-write')):
                raise ValueError('native-config-volume-foreign-consumer')
    if record is not None:
        if labels.get(LABEL) != record['nonce'] or (record['state'] == 'ready'
                and record.get('fingerprint') != fingerprint(row)):
            raise ValueError('native-config-volume-identity-changed')
        if record['state'] == 'creating':
            record.update(state='ready', fingerprint=fingerprint(row))
            _write(path, record, previous)
    # Unreceipted pre-existing storage may be reused, but never gains producer
    # custody merely because its name and normal Compose labels match.
    return record
