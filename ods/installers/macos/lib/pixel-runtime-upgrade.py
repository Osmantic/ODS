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
CORE_ROLES = ('gateway', 'access', 'relay')
LEGACY_NATIVE_ROLES = ('operations', 'promoter', 'manager') + CORE_ROLES
NATIVE_ROLES = ('integrations',) + LEGACY_NATIVE_ROLES
# One closed code/metadata family. No broker data, credentials, environment,
# definitions, logs or identity records are members of this rollback family.
SOURCE_ADDITIONS = frozenset({
    '/usr/local/libexec/ods-pixel-access/pixel_connections/' + name for name in (
        '__init__.py', 'activity.py', 'approvals.py', 'catalog.py', 'converge.py',
        'coordinator.py', 'custody.py', 'drafts.py', 'google_app.py', 'host_api.py', 'lifecycle.py',
        'native_access.py', 'native_apps.py', 'native_contract.py', 'native_enrollment.py', 'native_host_api.py',
        'native_mcp.py', 'native_public.py', 'native_executor.py', 'native_retirement.py', 'source_backend.py',
        'oauth.py', 'planner.py', 'plugins.py', 'state.py', 'status.py')
} | {
    '/usr/local/libexec/ods-pixel-access/' + name for name in (
        'connection_transaction.py', 'native_mcp_owner.py', 'native_mcp_oauth.mjs', 'pixel_connection_agent_status.py')
} | {
    '/private/etc/ods/pixel-provider-runtime-manifest.json',
    '/private/etc/ods/pixel-provider-runtime.json',
    '/private/var/lib/ods-pixel-access/source-runtime-installation.json',
})
SOURCE_PRIVATE_RECEIPT = '/private/var/lib/ods-pixel-access/source-runtime-installation.json'
PHASES = {'prepared', 'replacing-files', 'active', 'rolling-back', 'restored', 'recovery-required',
          *(action + '-' + role for action in ('stopping', 'starting')
            for role in NATIVE_ROLES)}


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
        for version in ('previous', 'candidate') for role in NATIVE_ROLES}
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


INSPECTION_ADDITIONS = frozenset('/usr/local/libexec/ods-pixel-services/helpers/' + name for name in (
    'preview_inspection.py', 'preview_inspection_protocol.py', 'workspace_preview.py',
    'unix_peer.py', 'preview-inspection.json'))
INTEGRATION_ADDITIONS = frozenset({
    *('/usr/local/libexec/ods-pixel-services/integrations/' + name for name in (
        'integrations_common.py', 'integrations_mcp.py', 'integrations_notes.py',
        'integrations_service.py', 'unix_peer.py', 'integrations_app_service.py',
        'integrations_apps.py', 'integrations_ntfy.py', 'integrations_recovery.py')),
    '/Library/LaunchDaemons/com.ods.pixel-native-integrations.plist'})


def source_family_hash(records):
    """Hash exact candidate images for the complete fixed Source family."""
    selected = [item for item in records if item['path'] in SOURCE_ADDITIONS]
    family = {item['path']: item for item in selected}
    if set(family) != SOURCE_ADDITIONS or len(selected) != len(SOURCE_ADDITIONS):
        raise UpgradeError('runtime-upgrade-source-family-incomplete')
    tuples = []
    for path, item in sorted(family.items()):
        expected_mode = 0o600 if path == SOURCE_PRIVATE_RECEIPT else 0o644
        if (type(item['after']) is not bytes or type(item['mode']) is not int or item['mode'] != expected_mode
                or type(item['gid']) is not int or item['gid'] != 0):
            raise UpgradeError('runtime-upgrade-source-family-invalid')
        tuples.append([path, item['mode'], item['gid'], hashlib.sha256(item['after']).hexdigest()])
    return hashlib.sha256(json.dumps(tuples, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _source_addition(value, records, *, current_digest, candidate_digest):
    """A serialized proof binding, never an independent filesystem admission.

    The installer must separately re-run its fixed custody verifier before each
    write/deletion. Codec acceptance cannot establish current quiescence.
    """
    try:
        withdrawal = (type(value) is dict and type(value.get('proof')) is dict
            and value['proof'].get('kind') == 'ods-native-source-installer-withdrawal-intent')
        fields = {'schemaVersion', 'kind', 'contextSha256', 'familySha256', 'proof', 'absentPaths'}
        if withdrawal and 'receipt' in value:
            fields.add('receipt')
        if (type(value) is not dict or set(value) != fields
                or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                or value['kind'] != 'ods-native-source-installer-addition'
                or any(type(value[key]) is not str or not re.fullmatch('[a-f0-9]{64}', value[key])
                       for key in ('contextSha256', 'familySha256'))
                or value['familySha256'] != source_family_hash(records)):
            raise ValueError()
        absent = sorted(item['path'] for item in records
                        if item['path'] in SOURCE_ADDITIONS and item['before'] is None)
        if type(value['absentPaths']) is not list or value['absentPaths'] != absent:
            raise ValueError()
        proof = value['proof']
        if withdrawal:
            transaction = {'kind': 'native-installer-upgrade', 'transaction': value['contextSha256'],
                'currentDigest': current_digest, 'candidateDigest': candidate_digest}
            owner = proof.get('owner')
            if type(owner) is not dict or set(owner) != {'uid', 'gid'}:
                raise ValueError()
            try:
                from pixel_connections.custody import (CustodyError, native_installer_withdrawal_intent,
                    native_installer_withdrawal_receipt)
            except ImportError as error:
                raise UpgradeError('runtime-upgrade-source-withdrawal-validator-unavailable') from error
            try:
                if native_installer_withdrawal_intent(proof, transaction=transaction,
                        owner_uid=owner['uid'], owner_gid=owner['gid']) != proof:
                    raise ValueError()
                if 'receipt' in value and native_installer_withdrawal_receipt(value['receipt'], intent=proof,
                        transaction=transaction, owner_uid=owner['uid'], owner_gid=owner['gid']) != value['receipt']:
                    raise ValueError()
            except CustodyError as error:
                raise UpgradeError('runtime-upgrade-source-withdrawal-proof-invalid') from error
            encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            if len(encoded) > 128 * 1024:
                raise ValueError()
            return json.loads(encoded)
        fields = {'schemaVersion', 'kind', 'transaction', 'currentDigest', 'candidateDigest',
            'ownerUid', 'ownerGid', 'oldRuntimeCustodySha256', 'runtimeRoot', 'sourceRoot',
            'kit', 'metadata', 'identity', 'witness'}
        if (type(proof) is not dict or set(proof) != fields
                or type(proof['schemaVersion']) is not int or proof['schemaVersion'] != 1
                or proof['kind'] != 'ods-native-source-installer-absence'
                or proof['transaction'] != value['contextSha256']
                or proof['currentDigest'] != current_digest or proof['candidateDigest'] != candidate_digest
                or current_digest == candidate_digest
                or any(type(proof[key]) is not str or not re.fullmatch('[a-f0-9]{64}', proof[key])
                       for key in ('currentDigest', 'candidateDigest'))
                or type(proof['ownerUid']) is not int or proof['ownerUid'] <= 0
                or type(proof['ownerGid']) is not int or proof['ownerGid'] < 0
                or type(proof['oldRuntimeCustodySha256']) is not str
                or not re.fullmatch('[a-f0-9]{64}', proof['oldRuntimeCustodySha256'])
                or any(type(proof[key]) is not dict for key in ('metadata', 'witness'))
                or proof['kit'] is not None and type(proof['kit']) is not dict
                or proof['identity'] is not None and type(proof['identity']) is not dict):
            raise ValueError()
        for key in ('runtimeRoot', 'sourceRoot'):
            path = proof[key]
            if (type(path) is not str or not path.startswith('/')
                    or any(part in ('', '.', '..') for part in path.split('/')[1:])
                    or any(ord(char) < 32 for char in path)):
                raise ValueError()
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if len(encoded) > 128 * 1024:
            raise ValueError()
        return json.loads(encoded)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise UpgradeError('runtime-upgrade-source-proof-invalid') from error


def encode_recovery(records, *, current_digest, candidate_digest, allowed_paths, source_addition=None):
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
                or type(item['mode']) is not int or item['mode'] not in (0o600, 0o640, 0o644, 0o755)
                or type(item['gid']) is not int or item['gid'] < 0):
            raise UpgradeError('runtime-upgrade-record-invalid')
        record = {key: item[key] for key in ('path', 'mode', 'gid')}
        for key in ('before', 'after'):
            body = item[key]
            source_absent = source_addition is not None and path in SOURCE_ADDITIONS
            if key == 'before' and body is None and (path in INSPECTION_ADDITIONS | INTEGRATION_ADDITIONS or source_absent):
                expected_mode = 0o600 if source_absent and path == SOURCE_PRIVATE_RECEIPT else 0o644
                if item['mode'] != expected_mode or item['gid'] != 0:
                    raise UpgradeError('runtime-upgrade-record-invalid')
                record[key] = record[key + 'Sha256'] = None
                continue
            if type(body) is not bytes or len(body) > 8 * 1024 * 1024:
                raise UpgradeError('runtime-upgrade-record-invalid')
            record[key] = base64.b64encode(body).decode('ascii')
            record[key + 'Sha256'] = hashlib.sha256(body).hexdigest()
        encoded.append(record)
    absent = {item['path'] for item in records if item['before'] is None}
    legacy_absent = absent - SOURCE_ADDITIONS if source_addition is not None else absent
    version = 4 if source_addition is not None else 3 if absent & INTEGRATION_ADDITIONS else 2 if absent else 1
    if legacy_absent and legacy_absent not in (INSPECTION_ADDITIONS, INTEGRATION_ADDITIONS,
                                INSPECTION_ADDITIONS | INTEGRATION_ADDITIONS):
        raise UpgradeError('runtime-upgrade-inspection-additions-incomplete')
    value = dict(schemaVersion=version, currentDigest=current_digest, candidateDigest=candidate_digest,
                 phase='prepared', files=encoded)
    if source_addition is not None:
        value['sourceAddition'] = _source_addition(source_addition, records,
            current_digest=current_digest, candidate_digest=candidate_digest)
    if len(json.dumps(value).encode()) > JOURNAL_LIMIT:
        raise UpgradeError('runtime-upgrade-journal-too-large')
    return value


def decode_recovery(value, *, current_digest, candidate_digest, allowed_paths):
    try:
        source = type(value) is dict and value.get('schemaVersion') == 4
        fields = {'schemaVersion', 'currentDigest', 'candidateDigest', 'phase', 'files'}
        if source:
            fields.add('sourceAddition')
        if (type(value) is not dict or set(value) != fields
                or type(value['schemaVersion']) is not int or value['schemaVersion'] not in (1, 2, 3, 4)
                or value['currentDigest'] != current_digest or value['candidateDigest'] != candidate_digest
                or value['phase'] not in PHASES or type(value['files']) is not list):
            raise ValueError()
        records = []
        for item in value['files']:
            if set(item) != {'path', 'mode', 'gid', 'before', 'after', 'beforeSha256', 'afterSha256'}:
                raise ValueError()
            record = {key: item[key] for key in ('path', 'mode', 'gid')}
            for key in ('before', 'after'):
                if key == 'before' and value['schemaVersion'] in (2, 3, 4) and item[key] is None:
                    if item[key + 'Sha256'] is not None:
                        raise ValueError()
                    record[key] = None
                    continue
                body = base64.b64decode(item[key], validate=True)
                if hashlib.sha256(body).hexdigest() != item[key + 'Sha256']:
                    raise ValueError()
                record[key] = body
            records.append(record)
        canonical = encode_recovery(records, current_digest=current_digest,
            candidate_digest=candidate_digest, allowed_paths=allowed_paths,
            source_addition=value['sourceAddition'] if source else None)
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

    def bind_source_withdrawal(self, receipt, *, current_digest, candidate_digest,
                               allowed_paths, verify_withdrawal):
        """CAS only prepared intent -> completed, independently checked receipt.

        A lost write acknowledgement observes the exact same committed value.
        No snapshot, target, context, family, nonce or prior intent is replaced.
        """
        from pixel_access_bridge import atomic_json, private_json
        if (self.value.get('schemaVersion') != 4 or self.value.get('phase') != 'prepared'
                or not callable(verify_withdrawal)
                or self.value.get('sourceAddition', {}).get('proof', {}).get('kind') !=
                   'ods-native-source-installer-withdrawal-intent'):
            raise UpgradeError('runtime-upgrade-source-withdrawal-binding-required')
        records = decode_recovery(self.value, current_digest=current_digest,
            candidate_digest=candidate_digest, allowed_paths=allowed_paths)
        addition = self.value['sourceAddition']
        if 'receipt' in addition and addition['receipt'] != receipt:
            raise UpgradeError('runtime-upgrade-source-withdrawal-receipt-changed')
        proposed = dict(self.value, sourceAddition=dict(addition, receipt=receipt))
        decode_recovery(proposed, current_digest=current_digest,
            candidate_digest=candidate_digest, allowed_paths=allowed_paths)
        current = private_json(self.path, 0, JOURNAL_LIMIT)
        if current not in (self.value, proposed):
            raise UpgradeError('runtime-upgrade-journal-changed')
        verify_withdrawal(intent=addition['proof'], receipt=receipt)
        if private_json(self.path, 0, JOURNAL_LIMIT) != current:
            raise UpgradeError('runtime-upgrade-journal-changed')
        if current != proposed:
            atomic_json(self.path, proposed)
        if private_json(self.path, 0, JOURNAL_LIMIT) != proposed:
            raise UpgradeError('runtime-upgrade-journal-changed')
        self.value = proposed
        verify_withdrawal(intent=addition['proof'], receipt=receipt)
        return records

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


def _source_mutation_guard(records, addition, verify):
    source_records = any(item['path'] in SOURCE_ADDITIONS for item in records)
    if addition is None:
        if source_records:
            raise UpgradeError('runtime-upgrade-source-proof-required')
        return False
    proof = addition.get('proof') if type(addition) is dict else None
    if type(proof) is not dict:
        raise UpgradeError('runtime-upgrade-source-proof-invalid')
    withdrawal = proof.get('kind') == 'ods-native-source-installer-withdrawal-intent'
    transaction = proof.get('transaction') if withdrawal else proof
    if type(transaction) is not dict:
        raise UpgradeError('runtime-upgrade-source-proof-invalid')
    _source_addition(addition, records, current_digest=transaction.get('currentDigest'),
                     candidate_digest=transaction.get('candidateDigest'))
    if withdrawal and 'receipt' not in addition:
        raise UpgradeError('runtime-upgrade-source-complete-withdrawal-required')
    if not callable(verify):
        raise UpgradeError('runtime-upgrade-source-fresh-proof-required')
    return True


def replace_deployment_files(records, *, read, replace, source_addition=None,
                             verify_source=None, write_source_added=None):
    """Apply pre-journaled file snapshots; each replace rechecks expected bytes."""
    if not records or len({item['path'] for item in records}) != len(records):
        raise UpgradeError('runtime-upgrade-file-set-invalid')
    source = _source_mutation_guard(records, source_addition, verify_source)
    if source and any(item['path'] in SOURCE_ADDITIONS and item['before'] is None for item in records) and not callable(write_source_added):
        raise UpgradeError('runtime-upgrade-source-writer-required')
    for item in records:
        if read(item['path']) != item['before']:
            raise UpgradeError('runtime-upgrade-file-drift')
    for item in records:
        if item['before'] != item['after']:
            if source and item['path'] in SOURCE_ADDITIONS:
                verify_source()
                if item['before'] is None:
                    write_source_added(item['path'], expected=None, replacement=item['after'],
                                       mode=item['mode'], gid=item['gid'])
                    continue
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


def restore_deployment_files(records, *, read, replace, source_addition=None,
                             verify_source=None, remove_source_added=None):
    """Restore only known previous/candidate bytes, including partial writes."""
    if not records or len({item['path'] for item in records}) != len(records):
        raise UpgradeError('runtime-upgrade-file-set-invalid')
    source = _source_mutation_guard(records, source_addition, verify_source)
    if source and any(item['path'] in SOURCE_ADDITIONS and item['before'] is None for item in records) and not callable(remove_source_added):
        raise UpgradeError('runtime-upgrade-source-remover-required')
    current = {}
    for item in records:
        value = read(item['path'])
        if value not in (item['before'], item['after']):
            raise UpgradeError('runtime-upgrade-rollback-file-drift')
        current[item['path']] = value
    for item in reversed(records):
        if current[item['path']] != item['before']:
            if source and item['path'] in SOURCE_ADDITIONS:
                verify_source()
                if item['before'] is None:
                    remove_source_added(item['path'], expected=item['after'], mode=item['mode'], gid=item['gid'])
                    continue
            replace(item['path'], expected=item['after'], replacement=item['before'],
                    mode=item['mode'], gid=item['gid'])


def service_roles(previous, candidate):
    """Accept the core deployment or the complete managed native deployment.

    Native helpers start before the gateway and stop after ingress closes.
    A partial native set cannot be treated as a successful joint update.
    """
    for roles in (CORE_ROLES, LEGACY_NATIVE_ROLES, NATIVE_ROLES):
        if set(previous) == set(candidate) == set(roles):
            return roles
    raise UpgradeError('runtime-upgrade-services-incomplete')


def recover_previous(*, previous, candidate, phase, verify_snapshots, observe,
                     assert_absent, restore_files, start, stop, ready, restore_owner=None):
    """Restore an interrupted replacement from fresh service observations.

    Caller holds the controller lock and admission hold. observe must qualify
    loaded definitions/processes against snapshots, returning previous,
    candidate, or absent. assert_absent must prove process-tree termination,
    not just absence of a launchd job. Unknown states fail before any stop.
    """
    roles = service_roles(previous, candidate)
    if (len({s.target for s in previous.values()}) != len(roles)
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
    """Replace the complete selected service set and restore on ordinary failures.

    Durable crash recovery is the caller's responsibility; this function never
    reports success after an interrupted or partially restored transition.
    """
    roles = service_roles(previous, candidate)
    if ((migrate_owner is None) != (restore_owner is None)
            or migrate_owner is not None and (not callable(migrate_owner) or not callable(restore_owner))):
        raise UpgradeError('runtime-upgrade-owner-hooks-incomplete')
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
        # Close ingress, controller and gateway before stopping native helpers.
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
