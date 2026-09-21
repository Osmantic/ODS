"""Native runtime replacement sequence; no CLI until custody adapter is complete.

The caller must hold the access-controller lock, persist rollback material,
verify both deployments and provide durable phase/write operations. Service
adapters use the same launchd identity checks as normal access transitions.
"""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
import re
import stat


JOURNAL_LIMIT = 32 * 1024 * 1024
PHASES = {'prepared', 'replacing-files', 'active', 'rolling-back', 'restored', 'recovery-required',
          *(action + '-' + role for action in ('stopping', 'starting')
            for role in ('gateway', 'access', 'relay'))}


class UpgradeError(RuntimeError):
    pass


def archive_restored_attempt(state, *, current_digest, candidate_digest, snapshots, verify_restored):
    """Protected filesystem adapter; caller holds the controller lock throughout."""
    from pixel_macos_custody import protected_directory, _verify_fd
    with protected_directory(state) as directory:
        if stat.S_IMODE(os.fstat(directory).st_mode) != 0o700:
            raise UpgradeError('runtime-upgrade-retirement-state-unsafe')

        def read(name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                info = _verify_fd(fd, directory=False)
                if stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 3 * JOURNAL_LIMIT:
                    raise UpgradeError('runtime-upgrade-retirement-file-unsafe')
                with os.fdopen(fd, 'rb', closefd=False) as handle:
                    body = handle.read(3 * JOURNAL_LIMIT + 1)
                after = os.fstat(fd)
                if (len(body) != info.st_size or (info.st_mtime_ns, info.st_ctime_ns) !=
                        (after.st_mtime_ns, after.st_ctime_ns)):
                    raise UpgradeError('runtime-upgrade-retirement-drift')
                return body
            finally:
                os.close(fd)

        saved = []
        def preserve(history):
            body = (json.dumps(history, sort_keys=True, separators=(',', ':')) + '\n').encode()
            name = 'runtime-upgrade-history-' + hashlib.sha256(body).hexdigest() + '.json'
            try:
                existing = read(name)
            except FileNotFoundError:
                temporary = '.retirement-' + os.urandom(16).hex()
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
                try:
                    with os.fdopen(fd, 'wb') as handle:
                        handle.write(body)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory,
                            follow_symlinks=False)
                finally:
                    os.unlink(temporary, dir_fd=directory)
                    os.fsync(directory)
                existing = read(name)
            if existing != body:
                raise UpgradeError('runtime-upgrade-retirement-history-conflict')
            saved.append(name)

        def remove(name, expected):
            if read(name) != expected:
                raise UpgradeError('runtime-upgrade-retirement-drift')
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)

        retire_restored_attempt(current_digest=current_digest, candidate_digest=candidate_digest,
            snapshots=snapshots, verify_restored=verify_restored,
            read=read, preserve=preserve, remove=remove)
        return saved[0]


def validate_retirement_snapshots(snapshots, *, current_digest, candidate_digest):
    """Validate fixed retirement scope independently of the storage adapter."""
    if (any(type(d) is not str or not re.fullmatch('[a-f0-9]{64}', d)
            for d in (current_digest, candidate_digest)) or current_digest == candidate_digest):
        raise UpgradeError('runtime-upgrade-digest-invalid')
    archive = 'runtime-upgrade-' + candidate_digest + '.completed.json'
    context = 'runtime-upgrade-context-' + candidate_digest + '.json'
    edge = 'runtime-upgrade-edge-' + candidate_digest + '.json'
    allowed = {archive, context, edge} | {
        'runtime-upgrade-stop-' + candidate_digest + '-' + version + '-' + role + '.json'
        for version in ('previous', 'candidate') for role in ('gateway', 'access', 'relay')}
    if (type(snapshots) is not dict or not {archive, context}.issubset(snapshots)
            or not set(snapshots).issubset(allowed)
            or any(type(body) is not bytes for body in snapshots.values())
            or sum(len(body) for body in snapshots.values()) > 2 * JOURNAL_LIMIT):
        raise UpgradeError('runtime-upgrade-retirement-invalid')
    try:
        completed = json.loads(snapshots[archive])
        if (completed['phase'] != 'restored' or completed['currentDigest'] != current_digest
                or completed['candidateDigest'] != candidate_digest):
            raise ValueError()
        if edge in snapshots and json.loads(snapshots[edge])['phase'] != 'released':
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise UpgradeError('runtime-upgrade-retirement-not-restored') from None
    return archive


def decode_retirement_history(body, *, history_digest, current_digest, candidate_digest):
    """Decode protected history selected by an independently supplied digest."""
    if (type(body) is not bytes or len(body) > 3 * JOURNAL_LIMIT
            or type(history_digest) is not str or not re.fullmatch('[a-f0-9]{64}', history_digest)
            or hashlib.sha256(body).hexdigest() != history_digest):
        raise UpgradeError('runtime-upgrade-retirement-history-invalid')
    try:
        value = json.loads(body)
        if (type(value) is not dict or set(value) != {'schemaVersion', 'currentDigest', 'candidateDigest', 'files'}
                or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                or value['currentDigest'] != current_digest or value['candidateDigest'] != candidate_digest
                or type(value['files']) is not dict
                or any(type(encoded) is not str for encoded in value['files'].values())):
            raise ValueError()
        snapshots = {name: base64.b64decode(encoded, validate=True) for name, encoded in value['files'].items()}
        if any(base64.b64encode(snapshots[name]).decode('ascii') != encoded
               for name, encoded in value['files'].items()):
            raise ValueError()
        if (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode() != body:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise UpgradeError('runtime-upgrade-retirement-history-invalid') from None
    validate_retirement_snapshots(snapshots, current_digest=current_digest, candidate_digest=candidate_digest)
    return snapshots


def retire_restored_attempt(*, current_digest, candidate_digest, snapshots,
                           verify_restored, read, preserve, remove):
    """Retire verified snapshots under lock; preserve is durable before remove.

    After interruption the caller obtains snapshots from protected history and
    keeps new upgrades blocked until removal finishes. Missing files can then
    be skipped. The live verifier must accept this partially retired state.
    """
    archive = validate_retirement_snapshots(snapshots, current_digest=current_digest,
                                           candidate_digest=candidate_digest)
    verify_restored()
    for name, body in snapshots.items():
        try:
            actual = read(name)
        except FileNotFoundError:
            continue
        if actual != body:
            raise UpgradeError('runtime-upgrade-retirement-drift')
    history = dict(schemaVersion=1, currentDigest=current_digest, candidateDigest=candidate_digest,
                   files={name: base64.b64encode(body).decode('ascii')
                          for name, body in sorted(snapshots.items())})
    # No unlink is permitted until the entire rollback authority is durable.
    preserve(history)
    verify_restored()
    # Keep the completed receipt until last for recovery discovery.
    for name in sorted(set(snapshots) - {archive}) + [archive]:
        try:
            remove(name, snapshots[name])
        except FileNotFoundError:
            pass


def encode_recovery(records, *, current_digest, candidate_digest, allowed_paths):
    """Bounded private rollback image. Paths must come from the verified plan."""
    if (any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value)
            for value in (current_digest, candidate_digest)) or current_digest == candidate_digest):
        raise UpgradeError('runtime-upgrade-digest-invalid')
    if (not records or len(records) > 128
            or len({item['path'] for item in records}) != len(records)
            or {item['path'] for item in records} != set(allowed_paths)):
        raise UpgradeError('runtime-upgrade-file-set-invalid')
    encoded = []
    for item in records:
        path = item['path']
        if (not isinstance(path, str) or not path.startswith('/')
                or any(part in ('', '.', '..') for part in path.split('/')[1:])
                or any(ord(c) < 32 for c in path)
                or type(item['mode']) is not int or item['mode'] not in (0o600, 0o644, 0o755)
                or type(item['gid']) is not int or item['gid'] < 0):
            raise UpgradeError('runtime-upgrade-record-invalid')
        record = {key: item[key] for key in ('path', 'mode', 'gid')}
        for key in ('before', 'after'):
            body = item[key]
            if type(body) is not bytes or len(body) > 8 * 1024 * 1024:
                raise UpgradeError('runtime-upgrade-record-invalid')
            record[key] = base64.b64encode(body).decode('ascii')
            record[key + 'Sha256'] = hashlib.sha256(body).hexdigest()
        encoded.append(record)
    value = dict(schemaVersion=1, currentDigest=current_digest, candidateDigest=candidate_digest,
                 phase='prepared', files=encoded)
    if len(json.dumps(value).encode()) > JOURNAL_LIMIT:
        raise UpgradeError('runtime-upgrade-journal-too-large')
    return value


def decode_recovery(value, *, current_digest, candidate_digest, allowed_paths):
    try:
        if (type(value) is not dict or set(value) != {
                'schemaVersion', 'currentDigest', 'candidateDigest', 'phase', 'files'}
                or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                or value['currentDigest'] != current_digest or value['candidateDigest'] != candidate_digest
                or value['phase'] not in PHASES or type(value['files']) is not list):
            raise ValueError()
        records = []
        for item in value['files']:
            if set(item) != {'path', 'mode', 'gid', 'before', 'after', 'beforeSha256', 'afterSha256'}:
                raise ValueError()
            record = {key: item[key] for key in ('path', 'mode', 'gid')}
            for key in ('before', 'after'):
                body = base64.b64decode(item[key], validate=True)
                if hashlib.sha256(body).hexdigest() != item[key + 'Sha256']:
                    raise ValueError()
                record[key] = body
            records.append(record)
        canonical = encode_recovery(records, current_digest=current_digest,
            candidate_digest=candidate_digest, allowed_paths=allowed_paths)
        canonical['phase'] = value['phase']
        if canonical != value:
            raise ValueError()
        return records
    except (ValueError, TypeError, KeyError) as error:
        raise UpgradeError('runtime-upgrade-journal-invalid') from error


class RecoveryJournal:
    """Use under the controller lock; never overwrite a prior recovery record."""
    def __init__(self, path, value):
        self.path, self.value = path, value

    @classmethod
    def create(cls, path, value):
        from pixel_access_bridge import atomic_json
        if os.path.lexists(path):
            raise UpgradeError('runtime-upgrade-journal-exists')
        atomic_json(path, value)
        return cls(path, value)

    @classmethod
    def load(cls, path, *, current_digest, candidate_digest, allowed_paths):
        """Reopen under the controller lock using independently approved identity."""
        from pixel_access_bridge import private_json
        value = private_json(path, 0, JOURNAL_LIMIT)
        records = decode_recovery(value, current_digest=current_digest,
            candidate_digest=candidate_digest, allowed_paths=allowed_paths)
        # Do not derive paths or approved digests from the record being read.
        # A changed record is never adopted as a new rollback authority.
        if private_json(path, 0, JOURNAL_LIMIT) != value:
            raise UpgradeError('runtime-upgrade-journal-changed')
        return cls(path, value), records

    def phase(self, phase):
        from pixel_access_bridge import atomic_json, private_json
        if phase not in PHASES:
            raise UpgradeError('runtime-upgrade-phase-invalid')
        if private_json(self.path, 0, JOURNAL_LIMIT) != self.value:
            raise UpgradeError('runtime-upgrade-journal-changed')
        updated = dict(self.value, phase=phase)
        atomic_json(self.path, updated)
        self.value = updated

    def finish(self, verify_live):
        """Archive rollback data before releasing the mutation guard, under lock."""
        from pixel_access_bridge import atomic_json, private_json
        if self.value['phase'] not in ('active', 'restored'):
            raise UpgradeError('runtime-upgrade-not-terminal')
        if private_json(self.path, 0, JOURNAL_LIMIT) != self.value:
            raise UpgradeError('runtime-upgrade-journal-changed')
        verify_live()
        if private_json(self.path, 0, JOURNAL_LIMIT) != self.value:
            raise UpgradeError('runtime-upgrade-journal-changed')
        archive = self.path.with_name('runtime-upgrade-' + self.value['candidateDigest'] + '.completed.json')
        if os.path.lexists(archive):
            if private_json(archive, 0, JOURNAL_LIMIT) != self.value:
                raise UpgradeError('runtime-upgrade-archive-conflict')
        else:
            atomic_json(archive, self.value)
        # A crash before unlink retains the guard; retry verifies live state
        # again and accepts only an identical, already durable archive.
        if private_json(self.path, 0, JOURNAL_LIMIT) != self.value:
            raise UpgradeError('runtime-upgrade-journal-changed')
        self.path.unlink()
        fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def prepared_upgrade(bridge, records, *, current_digest, candidate_digest, allowed_paths):
    """Share the controller's lock for the entire caller's transition."""
    value = encode_recovery(records, current_digest=current_digest,
                            candidate_digest=candidate_digest, allowed_paths=allowed_paths)
    with bridge.locked():
        if any(os.path.lexists(bridge.state / name) for name in
               ('transition.json', 'policy-activation.json', 'runtime-upgrade.json')):
            raise UpgradeError('runtime-upgrade-pending-recovery')
        journal = RecoveryJournal.create(bridge.state / 'runtime-upgrade.json', value)
        yield journal


def replace_deployment_files(records, *, read, replace):
    """Apply pre-journaled file snapshots; each replace rechecks expected bytes."""
    if not records or len({item['path'] for item in records}) != len(records):
        raise UpgradeError('runtime-upgrade-file-set-invalid')
    for item in records:
        if read(item['path']) != item['before']:
            raise UpgradeError('runtime-upgrade-file-drift')
    for item in records:
        if item['before'] != item['after']:
            replace(item['path'], expected=item['before'], replacement=item['after'],
                    mode=item['mode'], gid=item['gid'])


@contextmanager
def recovery_upgrade(bridge, *, current_digest, candidate_digest, allowed_paths, read):
    """Open interrupted rollback state under the same lock as live mutations.

    This is a preflight, not permission to reopen admission. The caller must
    retain the Edge hold and prove services stopped before restoring files.
    """
    with bridge.recovery_locked():
        if any(os.path.lexists(bridge.state / name) for name in
               ('transition.json', 'policy-activation.json')):
            raise UpgradeError('runtime-upgrade-pending-recovery')
        journal, records = RecoveryJournal.load(bridge.state / 'runtime-upgrade.json',
            current_digest=current_digest, candidate_digest=candidate_digest,
            allowed_paths=allowed_paths)
        for item in records:
            if read(item['path']) not in (item['before'], item['after']):
                raise UpgradeError('runtime-upgrade-rollback-file-drift')
        yield journal, records


def restore_deployment_files(records, *, read, replace):
    """Restore only known previous/candidate bytes, including partial writes."""
    if not records or len({item['path'] for item in records}) != len(records):
        raise UpgradeError('runtime-upgrade-file-set-invalid')
    current = {}
    for item in records:
        value = read(item['path'])
        if value not in (item['before'], item['after']):
            raise UpgradeError('runtime-upgrade-rollback-file-drift')
        current[item['path']] = value
    for item in reversed(records):
        if current[item['path']] != item['before']:
            replace(item['path'], expected=item['after'], replacement=item['before'],
                    mode=item['mode'], gid=item['gid'])


def recover_previous(*, previous, candidate, phase, verify_snapshots, observe,
                     assert_absent, restore_files, start, stop, ready, restore_owner=None):
    """Restore an interrupted replacement from fresh service observations.

    Caller holds the controller lock and admission hold. observe must qualify
    loaded definitions/processes against snapshots, returning previous,
    candidate, or absent. assert_absent must prove process-tree termination,
    not just absence of a launchd job. Unknown states fail before any stop.
    """
    roles = ('gateway', 'access', 'relay')
    if (set(previous) != set(roles) or set(candidate) != set(roles)
            or len({s.target for s in previous.values()}) != len(roles)
            or any(previous[r].target != candidate[r].target for r in roles)):
        raise UpgradeError('runtime-upgrade-services-incomplete')
    verify_snapshots()
    observed = {role: observe(previous[role], candidate[role]) for role in roles}
    if any(value not in ('previous', 'candidate', 'absent') for value in observed.values()):
        raise UpgradeError('runtime-upgrade-service-unqualified')
    phase('rolling-back')
    try:
        for role in reversed(roles):
            version = observed[role]
            if version == 'absent':
                assert_absent(previous[role], candidate[role])
            else:
                service = (previous if version == 'previous' else candidate)[role]
                stop(service)
                service.assert_stopped()
        # Recheck custody after stopping and before restoring any file.
        verify_snapshots()
        if restore_owner is not None:
            restore_owner()
        restore_files()
        for role in roles:
            start(previous[role])
        ready(previous)
        phase('restored')
    except BaseException as error:
        try:
            phase('recovery-required')
        except BaseException:
            pass
        raise UpgradeError('runtime-upgrade-recovery-required') from error


def activate(*, previous, candidate, phase, verify, replace_files, restore_files,
             start, stop, ready, migrate_owner=None, restore_owner=None):
    """Replace gateway/access/relay as a unit and restore on ordinary failures.

    Durable crash recovery is the caller's responsibility; this function never
    reports success after an interrupted or partially restored transition.
    """
    roles = ('gateway', 'access', 'relay')
    if ((migrate_owner is None) != (restore_owner is None)
            or migrate_owner is not None and (not callable(migrate_owner) or not callable(restore_owner))):
        raise UpgradeError('runtime-upgrade-owner-hooks-incomplete')
    if set(previous) != set(roles) or set(candidate) != set(roles):
        raise UpgradeError('runtime-upgrade-services-incomplete')
    for role in roles:
        if previous[role].target != candidate[role].target:
            raise UpgradeError('runtime-upgrade-target-changed')
    if len({service.target for service in previous.values()}) != len(roles):
        raise UpgradeError('runtime-upgrade-duplicate-target')
    verify()
    phase('prepared')
    stopped = []
    replacing = False
    attempted = []
    try:
        # Close the owner ingress first, then the controller, then the gateway.
        for role in reversed(roles):
            phase('stopping-' + role)
            stopped.append(role)
            stop(previous[role])
            previous[role].assert_stopped()
        verify()
        phase('replacing-files')
        replacing = True
        replace_files()
        if migrate_owner is not None:
            migrate_owner()
        for role in roles:
            phase('starting-' + role)
            attempted.append(role)
            start(candidate[role])
        ready(candidate)
        phase('active')
    except BaseException:
        try:
            phase('rolling-back')
            # A failed bootstrap may still have created a loaded job. Prove
            # it stopped before changing its executable/configuration back.
            for role in reversed(attempted):
                stop(candidate[role])
                candidate[role].assert_stopped()
            if not replacing:
                for role in stopped:
                    stop(previous[role])
                    previous[role].assert_stopped()
            if replacing:
                if restore_owner is not None:
                    restore_owner()
                restore_files()
            for role in roles:
                if role in stopped:
                    start(previous[role])
            ready(previous)
            phase('restored')
        except BaseException as recovery_error:
            try:
                phase('recovery-required')
            except BaseException:
                pass
            raise UpgradeError('runtime-upgrade-recovery-required') from recovery_error
        raise
