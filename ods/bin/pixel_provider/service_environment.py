"""Root-owned provider environment participant in the existing access transaction.

This performs real, reversible service-file writes. The outer coordinator owns
the root lock, admission, qualified runtime, owner configuration and restart.
Nothing here grants runtime custody or interprets an owner-supplied path. A
prepared environment is not an activated provider or successful inference.
"""
import os
import re
import stat
import tempfile
from pathlib import Path

from pixel_access_bridge import AccessError, atomic_json, digest
from pixel_access_protocol import HEX, provider_binding
from pixel_settings.coordinator import _read

from .managed_deployment import environment_bytes

ENVIRONMENT = Path('/etc/ods/pixel-provider.env')
DROPIN = Path('/etc/systemd/system/openclaw-gateway.service.d/95-ods-provider.conf')
RECORD = 'provider-service-environment.json'
MAX_FILE = 64 * 1024
MAX_RECORD = 600 * 1024
ROOT_UID = 0


def _parents(path):
    if not path.is_absolute() or path.resolve() != path:
        raise AccessError('unsafe-provider-service-path')
    for item in path.parents:
        info = item.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != ROOT_UID or info.st_mode & 0o022):
            raise AccessError('unsafe-provider-service-directory')


def _snapshot(path):
    _parents(path)
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != ROOT_UID
            or before.st_nlink != 1 or before.st_mode & 0o022):
        raise AccessError('unsafe-provider-service-file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid, opened.st_nlink) != (
                before.st_dev, before.st_ino, before.st_mode, ROOT_UID, 1):
            raise AccessError('provider-service-file-changed')
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            raw = handle.read(MAX_FILE + 1)
        after = os.fstat(fd)
        current = path.lstat()
        if (len(raw) > MAX_FILE or
                (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or
                (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)):
            raise AccessError('provider-service-file-changed')
        return {'hex': raw.hex(), 'mode': stat.S_IMODE(opened.st_mode)}
    finally:
        os.close(fd)


def _image(value):
    if value is None:
        return None
    if (type(value) is not dict or set(value) != {'hex', 'mode'}
            or type(value['mode']) is not int or value['mode'] not in (0o600, 0o644)
            or type(value['hex']) is not str or len(value['hex']) > MAX_FILE * 2):
        raise AccessError('invalid-provider-service-image')
    try:
        raw = bytes.fromhex(value['hex'])
    except ValueError:
        raise AccessError('invalid-provider-service-image') from None
    if raw.hex() != value['hex']:
        raise AccessError('invalid-provider-service-image')
    return value


def _pair(value):
    if type(value) is not dict or set(value) != {'environment', 'dropin'}:
        raise AccessError('invalid-provider-service-image')
    for item in value.values():
        _image(item)
    return value


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace(path, expected, image):
    if _snapshot(path) != expected:
        raise AccessError('provider-service-file-changed')
    if image == expected:
        return
    if image is None:
        path.unlink()
        _sync(path.parent)
        return
    fd, temporary = tempfile.mkstemp(prefix='.ods-provider-', dir=path.parent)
    try:
        os.fchmod(fd, image['mode'])
        with os.fdopen(fd, 'wb') as handle:
            handle.write(bytes.fromhex(image['hex']))
            handle.flush()
            os.fsync(handle.fileno())
        if _snapshot(path) != expected:
            raise AccessError('provider-service-file-changed')
        os.replace(temporary, path)
        _sync(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ServiceEnvironment:
    """Fixed service files; constructor overrides are for isolated qualification.

    Call only inside SystemdAccessBridge.locked(). Its root transition must exist
    before prepare. An old receipt cannot authorize a new transaction. Public
    requests must never select these paths or supply the private record.
    """
    def __init__(self, bridge, *, environment=ENVIRONMENT, dropin=DROPIN):
        self.bridge = bridge
        self.environment, self.dropin = Path(environment), Path(dropin)

    def snapshot(self):
        return _pair({'environment': _snapshot(self.environment), 'dropin': _snapshot(self.dropin)})

    def _journal(self, journal):
        if (type(journal) is not dict or journal.get('kind') != 'provider'
                or any(type(journal.get(key)) is not str or not HEX.fullmatch(journal[key])
                       for key in ('token', 'transactionId'))
                or self.bridge.pending() != journal):
            raise AccessError('provider-transition-changed')
        _parents(self.bridge.state / RECORD)

    def prepare(self, journal, *, before_sha, after_sha, before_binding, after_binding,
                deployment_document=None, policy=None, restore=None, expected=None):
        """Persist exact images before the owner writes its configuration.

        Initial adoption requires absent owned files. An update supplies the
        exact previous root-owned pair. Deactivation supplies the ORIGINAL root
        baseline, not whatever happens to be on disk when deactivating.
        """
        self._journal(journal)
        for value in (before_sha, after_sha):
            if type(value) is not str or not HEX.fullmatch(value):
                raise AccessError('invalid-provider-config-hash')
        if before_sha == after_sha:
            raise AccessError('ambiguous-provider-config-hash')
        provider_binding(before_binding)
        provider_binding(after_binding)
        before = self.snapshot()
        expected = _pair(expected) if expected is not None else {'environment': None, 'dropin': None}
        if before != expected:
            raise AccessError('provider-service-baseline-conflict')
        if after_binding is None:
            if deployment_document is not None or policy is not None or restore is None:
                raise AccessError('provider-service-restore-required')
            after = _pair(restore)
        else:
            if restore is not None or type(deployment_document) is not dict or deployment_document.get('binding') != after_binding:
                raise AccessError('provider-deployment-binding-mismatch')
            raw = environment_bytes(deployment_document, policy)
            # The path is host-selected, never caller/model selected. systemd
            # specifiers are not permitted in the fixed EnvironmentFile path.
            path = str(self.environment)
            if not re.fullmatch(r'/[A-Za-z0-9_./-]+', path):
                raise AccessError('unsafe-provider-service-path')
            directory = deployment_document['providerDirectory']
            if (not re.fullmatch(r'/[A-Za-z0-9_./-]+', directory)
                    or str(Path(directory)) != directory or Path(directory).resolve() != Path(directory)):
                raise AccessError('unsafe-provider-data-path')
            # EnvironmentFile= takes an absolute filename, not a shell-quoted
            # word. Actual systemd ignores a quoted path as non-absolute.
            # ProtectHome=tmpfs hides even owner-private files. Bind ONLY the
            # root-selected provider store, not its parent or the whole home.
            # This appends to existing mounts; deactivation removes our drop-in
            # and restores the exact original namespace. Tools retain their
            # independently verified sandbox/access policy.
            dropin = ('[Service]\nEnvironmentFile=' + path + '\nBindPaths=' + directory + '\n').encode()
            after = {'environment': {'hex': raw.hex(), 'mode': 0o600},
                     'dropin': {'hex': dropin.hex(), 'mode': 0o644}}
        _pair(after)  # Refuse oversized/generated images before arming recovery.
        record = {'schemaVersion': 1, 'transactionId': journal['transactionId'],
                  'beforeSha': before_sha, 'afterSha': after_sha,
                  'beforeBinding': before_binding, 'afterBinding': after_binding,
                  'before': before, 'after': after}
        # Do not overwrite recovery data for an already-armed operation.
        if journal.get('serviceEnvironment') is not None:
            raise AccessError('provider-service-already-prepared')
        atomic_json(self.bridge.state / RECORD, record)
        journal['serviceEnvironment'] = digest(record)
        atomic_json(self.bridge.state / 'transition.json', journal)
        return record

    def load(self, journal):
        self._journal(journal)
        record = _read(self.bridge.state / RECORD, ROOT_UID, MAX_RECORD)[0]
        keys = {'schemaVersion', 'transactionId', 'beforeSha', 'afterSha',
                'beforeBinding', 'afterBinding', 'before', 'after'}
        if (type(record) is not dict or set(record) != keys
                or type(record['schemaVersion']) is not int or record['schemaVersion'] != 1
                or record['transactionId'] != journal['transactionId']
                or digest(record) != journal.get('serviceEnvironment')
                or any(type(record[key]) is not str or not HEX.fullmatch(record[key]) for key in ('beforeSha', 'afterSha'))
                or record['beforeSha'] == record['afterSha']):
            raise AccessError('invalid-provider-service-record')
        for key in ('before', 'after'):
            _pair(record[key])
            provider_binding(record[key + 'Binding'])
        return record

    def select(self, journal):
        record = self.load(journal)
        _, checksum = _read(self.bridge.home / '.openclaw/openclaw.json', self.bridge.owner.pw_uid)
        if checksum == record['beforeSha']:
            side = 'before'
        elif checksum == record['afterSha']:
            side = 'after'
        else:
            raise AccessError('provider-config-changed')
        return record, side

    def apply(self, journal):
        """Select from ACTUAL config on every callback, including owner rollback.

        Partial-write recovery accepts only this transaction's exact file images.
        It never absorbs unrelated changes as a new baseline. Admission stays
        with the caller; this method does not release it or claim activation.
        """
        record, side = self.select(journal)
        current = self.snapshot()
        if any(current[key] not in (record['before'][key], record['after'][key]) for key in current):
            raise AccessError('provider-service-recovery-conflict')
        if (self.bridge.native('acquire', journal['token']).get('phase') != 'held'
                or self.bridge.edge('acquire', journal['token'], journal['edge_revision']).get('phase') != 'held'):
            raise AccessError('runtime-busy')
        native, edge = self.bridge.native(), self.bridge.edge()
        if native.get('active') != 0 or edge.get('streams') != 0:
            raise AccessError('runtime-busy')
        # Recheck after admission IO, before any root-owned file mutation.
        if self.select(journal)[1] != side:
            raise AccessError('provider-config-changed')
        desired = record[side]
        order = ('dropin', 'environment') if desired['dropin'] is None else ('environment', 'dropin')
        for key in order:
            _replace(getattr(self, key), current[key], desired[key])
        if self.snapshot() != desired or self.select(journal)[1] != side:
            raise AccessError('provider-service-file-changed')
        return {'side': side, 'binding': record[side + 'Binding'], 'environmentHash': digest(desired)}

    def verify(self, journal, selection):
        record, side = self.select(journal)
        if (selection != {'side': side, 'binding': record[side + 'Binding'], 'environmentHash': digest(record[side])}
                or self.snapshot() != record[side]):
            raise AccessError('provider-service-verification-changed')
        return record[side]
