"""Persist only approved native Compose bindings, retaining all other ODS data."""
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
from urllib.parse import urlsplit


SPEC = importlib.util.spec_from_file_location('native_env_values',
    Path(__file__).resolve().parents[3] / 'extensions/services/dashboard-api/env_values.py')
values = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(values)
KEYS = frozenset(('PIXEL_NATIVE_UID', 'PIXEL_INGRESS_GID', 'PIXEL_NATIVE_INGRESS_IMAGE',
    'PIXEL_NATIVE_CONFIG_PATH', 'PIXEL_NATIVE_WORKSPACE', 'PIXEL_NATIVE_GATEWAY_PORT',
    'PIXEL_NATIVE_ACCESS_PORT', 'PIXEL_INGRESS_RUNTIME_DIR', 'PIXEL_PREVIEW_RUNTIME_DIR'))
ASSIGNMENT = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$')
CREDENTIALS = frozenset(('DASHBOARD_API_KEY', 'PIXEL_OPENWEBUI_KEY', 'PIXEL_MODEL_RELAY_KEY'))


def snapshot(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > 2 * 1024 * 1024):
            raise ValueError('private-owner-environment-required')
        body = stream.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError('environment-too-large')
        return body, (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)


def persist(path, bindings, *, backup):
    if set(bindings) != KEYS or any(not isinstance(value, str) or not value or
            any(ord(char) < 32 for char in value) for value in bindings.values()):
        raise ValueError('complete-native-environment-bindings-required')
    return _persist(path, bindings, backup=backup)


def persist_migration(path, bindings, *, previous_config, backup, before_write=None):
    """Atomically add native bindings and import the existing local relay key.

    A migration never creates replacement dashboard or WebUI credentials. Return
    the exact written bytes so rollback cannot overwrite concurrent owner edits.
    """
    if set(bindings) != KEYS or any(not isinstance(value, str) or not value or
            any(ord(char) < 32 for char in value) for value in bindings.values()):
        raise ValueError('complete-native-environment-bindings-required')
    before, identity = snapshot(path)
    environment = {}
    for line in before.decode('utf-8').splitlines():
        match = ASSIGNMENT.fullmatch(line)
        if match and match[1] in CREDENTIALS | {'PIXEL_MODEL_RELAY_PORT'}:
            if match[1] in environment:
                raise ValueError('duplicate-native-credential')
            environment[match[1]] = values.parse_env_value(match[2])
    previous = snapshot(previous_config)
    credentials = {key: environment.get(key, '') for key in CREDENTIALS}
    credentials['PIXEL_MODEL_RELAY_KEY'] = relay_credential(json.loads(previous[0]), environment)
    if (len(set(credentials.values())) != 3
            or any(not re.fullmatch('[a-f0-9]{64}', value) for value in credentials.values())):
        raise ValueError('existing-distinct-native-credentials-required')
    def verify_previous():
        if snapshot(previous_config) != previous:
            raise ValueError('native-migration-source-config-changed')
    return _persist(path, {**bindings, **credentials}, backup=backup,
        expected=(before, identity), before_replace=verify_previous, return_bytes=True, before_write=before_write)


def relay_credential(previous, environment):
    """Reuse the active local relay identity; never import a remote credential."""
    try:
        provider = previous['models']['providers']['ods-gateway']
        key = provider['apiKey']
        route = urlsplit(provider['baseUrl'])
        existing = environment.get('PIXEL_MODEL_RELAY_KEY', '')
        valid = (isinstance(key, str) and re.fullmatch('[a-f0-9]{64}', key)
            and route.scheme == 'http' and route.hostname in ('127.0.0.1', 'localhost')
            and route.port == int(environment.get('PIXEL_MODEL_RELAY_PORT') or '4006')
            and route.path.rstrip('/') == '/v1' and not route.username and not route.password
            and not route.query and not route.fragment and (not existing or existing == key))
    except (KeyError, TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError('existing-model-relay-credential-needs-review')
    return key


def ensure_credentials(path, *, backup, previous_config=None, return_bytes=False):
    before, identity = snapshot(path)
    bindings, environment = {}, {}
    for line in before.decode('utf-8').splitlines():
        match = ASSIGNMENT.fullmatch(line)
        if match and match[1] in CREDENTIALS | {'PIXEL_MODEL_RELAY_PORT'}:
            key, raw = match.groups()
            if key in environment:
                raise ValueError('duplicate-native-credential')
            environment[key] = values.parse_env_value(raw)
            if key in CREDENTIALS:
                bindings[key] = environment[key]
    previous_snapshot = None
    if previous_config is not None:
        previous_snapshot = snapshot(previous_config)
        bindings['PIXEL_MODEL_RELAY_KEY'] = relay_credential(
            json.loads(previous_snapshot[0]), environment)
    existing = [value for value in bindings.values() if value]
    if (any(not re.fullmatch('[a-f0-9]{64}', value) for value in existing)
            or len(existing) != len(set(existing))):
        raise ValueError('distinct-native-stack-credentials-required')
    for key in sorted(CREDENTIALS):
        if not bindings.get(key):
            value = secrets.token_hex(32)
            while value in bindings.values():
                value = secrets.token_hex(32)
            bindings[key] = value
    def verify_previous():
        if previous_snapshot is not None and snapshot(previous_config) != previous_snapshot:
            raise ValueError('native-migration-source-config-changed')
    return _persist(path, bindings, backup=backup, expected=(before, identity),
        before_replace=verify_previous, return_bytes=return_bytes)


def _persist(path, bindings, *, backup, expected=None, before_replace=None, return_bytes=False, before_write=None):
    path, backup = Path(path), Path(backup)
    before, identity = snapshot(path)
    if expected is not None and expected != (before, identity):
        raise ValueError('environment-changed-during-native-configuration')
    seen, output = set(), []
    for line in before.decode('utf-8').splitlines(keepends=True):
        match = ASSIGNMENT.fullmatch(line.rstrip('\r\n'))
        if match and match[1] in bindings:
            key, raw = match.groups()
            if key in seen:
                raise ValueError('duplicate-native-environment-binding')
            seen.add(key)
            current = values.parse_env_value(raw)
            if current and current != bindings[key]:
                raise ValueError('existing-native-environment-conflict')
            if not current:
                line = key + '=' + values.quote_env_value(bindings[key]) + '\n'
        output.append(line)
    body = ''.join(output)
    missing = set(bindings) - seen
    if missing and body and not body.endswith('\n'):
        body += '\n'
    body += ''.join(key + '=' + values.quote_env_value(bindings[key]) + '\n' for key in sorted(missing))
    after = body.encode('utf-8')
    if before_replace is not None:
        before_replace()
    if after == before:
        return None if return_bytes else False
    if before_write is not None:
        before_write(after)
    with backup.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(before)
        stream.flush()
        os.fsync(stream.fileno())
    fd, temporary = tempfile.mkstemp(prefix='.native-env-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        if snapshot(path) != (before, identity):
            raise ValueError('environment-changed-during-native-configuration')
        if before_replace is not None:
            before_replace()
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return after if return_bytes else True


def restore(path, *, backup, expected):
    """Restore only the exact environment written by this transaction.

    The caller supplies its recorded post-write bytes. Concurrent owner edits
    require review instead of being overwritten by an automatic rollback.
    """
    if not isinstance(expected, bytes):
        raise ValueError('native-environment-rollback-bytes-required')
    path, backup = Path(path), Path(backup)
    original, backup_identity = snapshot(backup)
    current, identity = snapshot(path)
    if current == original:
        return False
    if current != expected:
        raise ValueError('environment-changed-before-native-rollback')
    fd, temporary = tempfile.mkstemp(prefix='.native-env-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        if (snapshot(path) != (current, identity)
                or snapshot(backup) != (original, backup_identity)):
            raise ValueError('environment-changed-before-native-rollback')
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return True
