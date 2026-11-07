"""Closed recovery codec and real owned file/CAS failure controls; no Darwin execution."""
import copy
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('source_installer_upgrade',
    ROOT / 'installers/macos/lib/pixel-runtime-upgrade.py')
upgrade = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade)
SPEC = importlib.util.spec_from_file_location('source_installer_access',
    ROOT / 'installers/macos/lib/pixel-macos-access-install.py')
access = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(access)
sys.path.insert(0, str(ROOT / 'bin'))
CURRENT, CANDIDATE = 'a' * 64, 'b' * 64


def selection(records):
    return dict(current_digest=CURRENT, candidate_digest=CANDIDATE,
                allowed_paths={item['path'] for item in records})


def source_images():
    return [dict(path=path, before=None, after=('candidate:' + path).encode(),
                 mode=0o600 if path == upgrade.SOURCE_PRIVATE_RECEIPT else 0o644, gid=0)
            for path in sorted(upgrade.SOURCE_ADDITIONS)]


def binding(records):
    proof = dict(schemaVersion=1, kind='ods-native-source-installer-absence', transaction='c' * 64,
        currentDigest=CURRENT, candidateDigest=CANDIDATE, ownerUid=501, ownerGid=20,
        oldRuntimeCustodySha256='d' * 64, runtimeRoot='/fixed/old/runtime', sourceRoot='/fixed/old/source',
        kit={}, metadata={}, identity=None, witness={})
    return dict(schemaVersion=1, kind='ods-native-source-installer-addition', contextSha256='c' * 64,
        familySha256=upgrade.source_family_hash(records), proof=proof,
        absentPaths=sorted(item['path'] for item in records if item['before'] is None))


def context_plan(records):
    owner = SimpleNamespace(pw_name='invented-owner', pw_uid=501, pw_gid=20, pw_dir='/Users/invented-owner')
    plan = {field: {} for field in access.RECOVERY_PLAN_FIELDS}
    plan.update(owner=owner, source_bytes=b'invented original plist', key=b'invented fixture key',
        upgrade_kind='workspace-root', access_port=18790,
        upgrade_qualification={'currentDigest': CURRENT, 'candidateDigest': CANDIDATE},
        runtime_bundle={'digest': CANDIDATE, 'source_config_bytes': b'invented old config',
                        'config_bytes': b'invented candidate config'})
    plan['source_lifecycle'] = dict(schemaVersion=1, kind='ods-native-source-installer-intent', mode='absent',
        nonce='e' * 32, currentDigest=CURRENT, candidateDigest=CANDIDATE,
        ownerUid=owner.pw_uid, ownerGid=owner.pw_gid, familySha256=upgrade.source_family_hash(records))
    return plan


def withdrawal_authority(records):
    transaction = dict(kind='native-installer-upgrade', transaction='c' * 64,
        currentDigest=CURRENT, candidateDigest=CANDIDATE)
    bound = dict(schemaVersion=1, kind='ods-native-source-environment', deploymentSha256='1' * 64,
        kitSha256='2' * 64, gate=[1, 10], ownerUid=501, brokerUid=62000,
        brokerGuid='AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE', runtimeCustodySha256='3' * 64,
        bundleSha256=CURRENT, sourceRef='f' * 40)
    intent = dict(schemaVersion=1, kind='ods-native-source-installer-withdrawal-intent',
        transaction=transaction, binding=bound,
        runtime=dict(custodySha256=bound['runtimeCustodySha256'], bundleSha256=CURRENT, sourceRef=bound['sourceRef']),
        owner=dict(uid=501, gid=20), generation='d' * 32,
        boot='11111111-2222-4333-8444-555555555555',
        environment=dict(inode=[1, 11], sha256='4' * 64, size=128),
        gate=dict(inode=[1, 12], sha256='5' * 64, mode=0o640, gid=62000),
        metadata=dict(identitySha256='6' * 64, prepareSha256='7' * 64,
            logsSha256='8' * 64, publicationSha256='9' * 64), token=None)
    receipt = {key: copy.deepcopy(intent[key]) for key in (
        'transaction', 'binding', 'runtime', 'owner', 'generation', 'environment', 'metadata', 'token')}
    receipt.update(schemaVersion=1, kind='ods-native-source-installer-withdrawal',
        intentSha256=hashlib.sha256((json.dumps(intent, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()).hexdigest(),
        withdrawal='e' * 32, archive=dict(name='.source-installer-withdrawal-' + 'e' * 32,
            inode=[1, 13], receiptSha256='a' * 64),
        descriptor=dict(inode=[1, 14], sha256='b' * 64, mode=0o640, gid=62000), boot=intent['boot'])
    addition = dict(schemaVersion=1, kind='ods-native-source-installer-addition', contextSha256='c' * 64,
        familySha256=upgrade.source_family_hash(records), proof=intent,
        absentPaths=sorted(r['path'] for r in records if r['before'] is None))
    return addition, receipt


def test_active_withdrawal_intent_is_serializable_but_cannot_authorize_any_code_mutation():
    records = source_images()
    addition, receipt = withdrawal_authority(records)
    value = upgrade.encode_recovery(records, source_addition=addition, **selection(records))
    assert upgrade.decode_recovery(value, **selection(records)) == records
    called = []
    with pytest.raises(upgrade.UpgradeError, match='complete-withdrawal-required'):
        upgrade.replace_deployment_files(records, read=lambda _p: None,
            replace=lambda *_a, **_kw: called.append(True), source_addition=addition,
            verify_source=lambda: called.append(True), write_source_added=lambda *_a, **_kw: called.append(True))
    assert not called
    complete = dict(addition, receipt=receipt)
    assert upgrade.decode_recovery(upgrade.encode_recovery(records, source_addition=complete,
        **selection(records)), **selection(records)) == records


@pytest.mark.parametrize('fault', ['token', 'runtime', 'intent-hash', 'archive', 'generation', 'extra'])
def test_complete_withdrawal_cannot_change_frozen_runtime_token_or_archive_authority(fault):
    records = source_images()
    addition, receipt = withdrawal_authority(records)
    if fault == 'token': receipt['token'] = [1, 2, 62000, 62000, 0o600, 100, 1, 1, 1]
    elif fault == 'runtime': receipt['runtime']['custodySha256'] = 'f' * 64
    elif fault == 'intent-hash': receipt['intentSha256'] = 'f' * 64
    elif fault == 'archive': receipt['archive']['name'] = '../foreign-archive'
    elif fault == 'generation': receipt['generation'] = 'f' * 32
    elif fault == 'extra': receipt['allowUpgrade'] = True
    with pytest.raises(upgrade.UpgradeError):
        upgrade.encode_recovery(records, source_addition=dict(addition, receipt=receipt), **selection(records))


@pytest.mark.parametrize('schedules', [False, True])
@pytest.mark.parametrize('fault', [None, 'lost-ack', 'changed-journal', 'new-token', 'changed-target'])
def test_prepared_withdrawal_binding_uses_real_private_journal_cas_and_observes_lost_ack(tmp_path, monkeypatch, schedules, fault):
    import pixel_access_bridge as bridge
    records = source_images()
    addition, receipt = withdrawal_authority(records)
    if schedules:
        records += [dict(path=path, before=None, after=b'schedule code', mode=0o644, gid=0)
                    for path in sorted(upgrade.SCHEDULE_ADDITIONS)]
    value = upgrade.encode_recovery(records, source_addition=addition, **selection(records))
    path = tmp_path / 'runtime-upgrade.json'
    journal = upgrade.RecoveryJournal.create(path, value)
    original_read, original_write = bridge.private_json, bridge.atomic_json
    monkeypatch.setattr(bridge, 'private_json', lambda path, _uid, limit: original_read(path, os.geteuid(), limit))
    writes, checks = [], []
    def write(target, body):
        writes.append(copy.deepcopy(body))
        original_write(target, body)
        if fault == 'lost-ack' and len(writes) == 1: raise OSError('owned fixture lost atomic-write acknowledgement')
    monkeypatch.setattr(bridge, 'atomic_json', write)
    def fresh(*, intent, receipt):
        checks.append(True)
        assert intent == addition['proof']
        if fault == 'new-token': raise ValueError('fresh token metadata changed; preserve state')
    if fault == 'changed-journal':
        original_write(path, dict(value, phase='starting-gateway'))
    if fault == 'changed-target':
        receipt['transaction']['candidateDigest'] = 'f' * 64
    def commit():
        return journal.bind_source_withdrawal(receipt, verify_withdrawal=fresh, **selection(records))
    if fault == 'lost-ack':
        with pytest.raises(OSError): commit()
        assert 'receipt' not in journal.value['sourceAddition']
        assert 'receipt' in original_read(path, os.geteuid(), upgrade.JOURNAL_LIMIT)['sourceAddition']
        assert commit() == records
        assert len(writes) == 1
    elif fault:
        with pytest.raises((ValueError, upgrade.UpgradeError)): commit()
        assert not writes
    else:
        assert commit() == records
        assert commit() == records
        assert len(writes) == 1
        for key in ('files', 'currentDigest', 'candidateDigest', 'phase'):
            assert journal.value[key] == value[key]
        assert journal.value['sourceAddition']['proof'] == addition['proof']
        assert checks


@pytest.mark.parametrize('fault', [None, 'owner', 'nonce', 'mode', 'candidate', 'unknown', 'schema-downgrade'])
def test_context_v2_preserves_intent_and_refuses_changed_or_signature_only_authority(monkeypatch, fault):
    plan = context_plan(source_images())
    saved = access._recovery_context(plan)
    assert saved['schemaVersion'] == 2
    if fault == 'owner': saved['source_lifecycle']['ownerUid'] = 502
    elif fault == 'nonce': saved['source_lifecycle']['nonce'] = 'not-a-frozen-nonce'
    elif fault == 'mode': saved['source_lifecycle']['mode'] = 'withdrawal-not-yet-qualified'
    elif fault == 'candidate': saved['source_lifecycle']['candidateDigest'] = 'e' * 64
    elif fault == 'unknown': saved['source_lifecycle']['allowUpgrade'] = True
    elif fault == 'schema-downgrade': saved['schemaVersion'] = 1
    monkeypatch.setattr(access.pwd, 'getpwnam', lambda _name: plan['owner'])
    if fault:
        with pytest.raises(access.InstallError):
            access._decode_recovery_context(saved, current_digest=CURRENT, candidate_digest=CANDIDATE,
                owner_name=plan['owner'].pw_name)
    else:
        assert access._decode_recovery_context(saved, current_digest=CURRENT, candidate_digest=CANDIDATE,
            owner_name=plan['owner'].pw_name) == plan


@pytest.mark.parametrize('schedules', [False, True])
@pytest.mark.parametrize('fault', [None, 'caller-hash', 'context-byte-change', 'journal-owner', 'journal-family', 'lost-context-ack'])
def test_publisher_transaction_reader_reopens_actual_context_and_journal_fds(tmp_path, monkeypatch, schedules, fault):
    """Actual files/CAS; bundle qualification is the explicit isolated dependency."""
    import pixel_access_bridge as bridge
    import pixel_macos_custody as custody
    records = source_images()
    plan = context_plan(records)
    value = access._recovery_context(plan)
    raw = (json.dumps(value, sort_keys=True) + '\n').encode()
    context = tmp_path / ('runtime-upgrade-context-' + CANDIDATE + '.json')
    context.write_bytes(raw)
    context.chmod(0o600)
    digest = hashlib.sha256(raw).hexdigest()
    authority = binding(records)
    if schedules:
        records += [dict(path=path, before=None, after=b'schedule code', mode=0o644, gid=0)
                    for path in sorted(upgrade.SCHEDULE_ADDITIONS)]
    authority['contextSha256'] = authority['proof']['transaction'] = digest
    if fault == 'journal-owner': authority['proof']['ownerUid'] = 502
    if fault == 'journal-family': plan['source_lifecycle']['familySha256'] = 'e' * 64
    journal_value = upgrade.encode_recovery(records, source_addition=authority, **selection(records))
    journal_path = tmp_path / 'runtime-upgrade.json'
    journal = upgrade.RecoveryJournal.create(journal_path, journal_value)
    monkeypatch.setattr(access._launchd, 'ACCESS_STATE', tmp_path)
    monkeypatch.setattr(access.pwd, 'getpwnam', lambda _name: plan['owner'])
    real_read = bridge.private_json
    monkeypatch.setattr(bridge, 'private_json', lambda path, _uid, maximum: real_read(path, os.geteuid(), maximum))
    monkeypatch.setattr(custody, 'protected_bytes', lambda path, *, limit: Path(path).read_bytes())
    calls = []
    def isolated_bundle(**kwargs):
        calls.append(kwargs)
        loaded, decoded = upgrade.RecoveryJournal.load(journal_path, **selection(records))
        if fault == 'lost-context-ack': context.write_bytes(raw + b' ')
        return plan, loaded, decoded
    monkeypatch.setattr(access, '_load_upgrade_recovery_base', isolated_bundle)
    transaction = dict(kind='native-installer-upgrade', transaction=digest,
        currentDigest=CURRENT, candidateDigest=CANDIDATE)
    if fault == 'caller-hash': transaction['transaction'] = 'f' * 64
    elif fault == 'context-byte-change': context.write_bytes(raw + b' ')
    if fault:
        with pytest.raises(access.InstallError): access.verify_source_installer_transaction(transaction)
    else:
        result = access.verify_source_installer_transaction(transaction)
        assert result['transaction'] == transaction and result['files'] == records
        assert result['intent'] == plan['source_lifecycle'] and result['phase'] == 'prepared'
        assert context.read_bytes() == raw
    assert real_read(journal_path, os.geteuid(), upgrade.JOURNAL_LIMIT) == journal.value


def test_source_family_matches_the_complete_production_flat_publication():
    """A new helper requires a deliberate codec change, never an unknown optional path."""
    import ast
    tree = ast.parse((ROOT / 'installers/macos/lib/pixel-macos-access-install.py').read_text())
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ('CONNECTION_FILES', 'SOURCE_HOST_FILES')}
    expected = {'/usr/local/libexec/ods-pixel-access/pixel_connections/' + name for name in constants['CONNECTION_FILES']}
    expected |= {'/usr/local/libexec/ods-pixel-access/' + name for name in constants['SOURCE_HOST_FILES']}
    expected |= {'/private/etc/ods/pixel-provider-runtime-manifest.json',
        '/private/etc/ods/pixel-provider-runtime.json', upgrade.SOURCE_PRIVATE_RECEIPT}
    assert upgrade.SOURCE_ADDITIONS == expected
    assert all('/pixel-source-broker/' not in path and 'token' not in path for path in expected)


@pytest.mark.parametrize('existing', [False, True])
def test_schema_four_roundtrip_binds_all_candidate_images_and_original_absence(existing):
    records = source_images()
    if existing:
        records[0]['before'] = b'exact old protected code'
    value = upgrade.encode_recovery(records, source_addition=binding(records), **selection(records))
    assert value['schemaVersion'] == 4
    assert upgrade.decode_recovery(json.loads(json.dumps(value)), **selection(records)) == records
    altered = copy.deepcopy(value)
    altered['sourceAddition']['absentPaths'].append('/private/var/lib/pixel-source-broker/private/google-token.json')
    with pytest.raises(upgrade.UpgradeError):
        upgrade.decode_recovery(altered, **selection(records))


@pytest.mark.parametrize('fault', ['duplicate', 'boolean-gid'])
def test_candidate_family_digest_refuses_ambiguous_or_coerced_attributes(fault):
    records = source_images()
    if fault == 'duplicate': records.append(dict(records[0]))
    else: records[0]['gid'] = False
    with pytest.raises(upgrade.UpgradeError): upgrade.source_family_hash(records)


@pytest.mark.parametrize('fault', ['no-proof', 'partial', 'foreign', 'target', 'context', 'family',
    'proof-kind', 'owner', 'proof-extra', 'absent-subset', 'private-file', 'receipt-mode'])
def test_source_additions_never_accept_mixed_unknown_or_unbound_images(fault):
    records = source_images()
    authority = binding(records)
    if fault == 'no-proof': authority = None
    elif fault == 'partial': records.pop()
    elif fault == 'foreign': records[0]['path'] = '/usr/local/libexec/foreign.py'
    elif fault == 'target': authority['proof']['candidateDigest'] = 'e' * 64
    elif fault == 'context': authority['proof']['transaction'] = 'e' * 64
    elif fault == 'family': records[0]['after'] += b'changed'
    elif fault == 'proof-kind': authority['proof']['kind'] = 'an-assertion-is-not-a-receipt'
    elif fault == 'owner': authority['proof']['ownerUid'] = 0
    elif fault == 'proof-extra': authority['proof']['allowUpgrade'] = True
    elif fault == 'absent-subset': authority['absentPaths'].pop()
    elif fault == 'private-file': records.append(dict(path='/private/var/lib/pixel-source-broker/source-broker.env',
        before=None, after=b'private data must never become a code rollback image', mode=0o600, gid=0))
    elif fault == 'receipt-mode': next(r for r in records if r['path'] == upgrade.SOURCE_PRIVATE_RECEIPT)['mode'] = 0o644
    with pytest.raises(upgrade.UpgradeError):
        upgrade.encode_recovery(records, source_addition=authority, **selection(records))


@pytest.mark.parametrize('version', [1, 2, 3])
def test_previous_recovery_schemas_still_roundtrip_without_source_authority(version):
    if version == 1:
        records = [dict(path='/fixed/existing.py', before=b'old', after=b'new', mode=0o644, gid=0)]
    else:
        family = upgrade.INSPECTION_ADDITIONS if version == 2 else upgrade.INTEGRATION_ADDITIONS
        records = [dict(path=p, before=None, after=b'new', mode=0o644, gid=0) for p in sorted(family)]
    value = upgrade.encode_recovery(records, **selection(records))
    assert value['schemaVersion'] == version
    assert upgrade.decode_recovery(value, **selection(records)) == records


@pytest.mark.parametrize('fault', [None, 'partial', 'existing-member', 'foreign', 'mode', 'gid',
    'downgrade', 'source-proof', 'source-family'])
def test_independent_source_reader_accepts_only_closed_schedule_schema_five(monkeypatch, fault):
    from pixel_connections import custody
    records = source_images()
    addition, _receipt = withdrawal_authority(records)
    records += [dict(path=path, before=None, after=b'schedule code', mode=0o644, gid=0)
                for path in sorted(upgrade.SCHEDULE_ADDITIONS)]
    value = upgrade.encode_recovery(records, source_addition=addition, **selection(records))
    assert value['schemaVersion'] == 5
    assert custody._native_installer_schedule_additions() == upgrade.SCHEDULE_ADDITIONS
    schedule = [item for item in value['files'] if item['path'] in upgrade.SCHEDULE_ADDITIONS]
    if fault == 'partial': value['files'].remove(schedule[0])
    elif fault == 'existing-member':
        schedule[0]['before'], schedule[0]['beforeSha256'] = schedule[0]['after'], schedule[0]['afterSha256']
    elif fault == 'foreign': schedule[0]['path'] = '/usr/local/libexec/ods-pixel-access/foreign.py'
    elif fault == 'mode': schedule[0]['mode'] = 0o600
    elif fault == 'gid': schedule[0]['gid'] = 20
    elif fault == 'downgrade': value['schemaVersion'] = 4
    elif fault == 'source-proof': value['sourceAddition']['proof']['transaction']['candidateDigest'] = 'e' * 64
    elif fault == 'source-family': value['sourceAddition']['familySha256'] = 'e' * 64
    raw = json.dumps(value).encode()
    monkeypatch.setattr(custody, '_native_root_file', lambda *_a, **_kw: (raw, (1, 2)))
    context = dict(source_lifecycle=dict(mode='withdrawal', familySha256=upgrade.source_family_hash(records)),
                   owner=dict(uid=501, gid=20))
    transaction = dict(kind='native-installer-upgrade', transaction='c' * 64,
                       currentDigest=CURRENT, candidateDigest=CANDIDATE)
    if fault:
        with pytest.raises(custody.CustodyError): custody._native_installer_journal(123, transaction, context)
    else:
        decoded, images, body = custody._native_installer_journal(123, transaction, context)
        assert decoded == value and images == records and body == raw


def owned_images(tmp_path):
    records = source_images()
    files = {r['path']: tmp_path / 'code' / str(index) for index, r in enumerate(records)}
    (tmp_path / 'code').mkdir()
    calls = []
    def read(path):
        return files[path].read_bytes() if files[path].exists() else None
    def create(path, *, expected, replacement, mode, gid):
        assert expected is None and not files[path].exists() and gid == 0
        fd = os.open(files[path], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        try:
            os.write(fd, replacement)
            os.fsync(fd)
        finally: os.close(fd)
        calls.append(('create', path))
    def remove(path, *, expected, mode, gid):
        assert read(path) == expected and path in upgrade.SOURCE_ADDITIONS and gid == 0
        assert files[path].stat().st_mode & 0o777 == mode
        files[path].unlink()
        calls.append(('remove', path))
    def reject_generic(*_args, **_kwargs):
        pytest.fail('generic file replacement cannot authorize Source additions/removal')
    return records, files, calls, read, create, remove, reject_generic


def test_source_code_write_requires_a_fresh_verifier_and_exact_writer(tmp_path):
    records, files, calls, read, create, _remove, generic = owned_images(tmp_path)
    authority = binding(records)
    for options in ({}, {'source_addition': authority}, {'source_addition': authority, 'verify_source': lambda: None}):
        with pytest.raises(upgrade.UpgradeError):
            upgrade.replace_deployment_files(records, read=read, replace=generic, **options)
    assert not calls and not list((tmp_path / 'code').iterdir())
    checked = []
    upgrade.replace_deployment_files(records, read=read, replace=generic, source_addition=authority,
        verify_source=lambda: checked.append(True), write_source_added=create)
    assert len(checked) == len(records) and all(read(r['path']) == r['after'] for r in records)


def test_preexisting_source_code_cannot_use_a_legacy_replacement_to_bypass_lifecycle():
    record = dict(source_images()[0], before=b'old protected source')
    calls = []
    with pytest.raises(upgrade.UpgradeError, match='source-proof-required'):
        upgrade.replace_deployment_files([record], read=lambda _path: record['before'],
            replace=lambda *_a, **_kw: calls.append(True))
    with pytest.raises(upgrade.UpgradeError, match='source-proof-required'):
        upgrade.restore_deployment_files([record], read=lambda _path: record['after'],
            replace=lambda *_a, **_kw: calls.append(True))
    assert not calls


@pytest.mark.parametrize('fault', ['new-actor', 'new-token', 'code-drift'])
def test_rollback_preserves_private_data_and_stops_on_new_state_or_changed_code(tmp_path, fault):
    records, files, calls, read, create, remove, generic = owned_images(tmp_path)
    authority = binding(records)
    upgrade.replace_deployment_files(records, read=read, replace=generic, source_addition=authority,
        verify_source=lambda: None, write_source_added=create)
    private = tmp_path / 'private-token'
    private.write_bytes(b'INVENTED_TOKEN_PRIVATE_DATA')
    inode = (private.stat().st_dev, private.stat().st_ino)
    before = {p: read(p) for p in files}
    def fresh():
        if fault in ('new-actor', 'new-token'):
            raise ValueError('new private state or actor requires preservation')
    if fault == 'code-drift': files[records[0]['path']].write_bytes(b'unknown protected image')
    mutations = len(calls)
    with pytest.raises((ValueError, upgrade.UpgradeError)):
        upgrade.restore_deployment_files(records, read=read, replace=generic, source_addition=authority,
            verify_source=fresh, remove_source_added=remove)
    assert len(calls) == mutations
    assert (private.stat().st_dev, private.stat().st_ino) == inode
    assert private.read_bytes() == b'INVENTED_TOKEN_PRIVATE_DATA'
    if fault != 'code-drift': assert {p: read(p) for p in files} == before


def test_interrupted_exact_code_rollback_can_resume_without_touching_private_inodes(tmp_path):
    records, files, calls, read, create, remove, generic = owned_images(tmp_path)
    authority = binding(records)
    upgrade.replace_deployment_files(records, read=read, replace=generic, source_addition=authority,
        verify_source=lambda: None, write_source_added=create)
    count = []
    def interrupted(path, **kwargs):
        remove(path, **kwargs)
        count.append(path)
        if len(count) == 2: raise OSError('owned fixture lost acknowledgement after exact unlink')
    with pytest.raises(OSError):
        upgrade.restore_deployment_files(records, read=read, replace=generic, source_addition=authority,
            verify_source=lambda: None, remove_source_added=interrupted)
    assert sum(p.exists() for p in files.values()) == len(records) - 2
    upgrade.restore_deployment_files(records, read=read, replace=generic, source_addition=authority,
        verify_source=lambda: None, remove_source_added=remove)
    assert not any(p.exists() for p in files.values())


@pytest.mark.parametrize('path', [
    '/private/var/lib/pixel-source-broker/private/google-token.json',
    '/private/var/lib/pixel-source-broker/source-broker.env',
    '/private/var/lib/ods-pixel-access/source-identity.json',
    '/Library/LaunchDaemons/com.ods.pixel-native-source-sync.plist',
    '/private/var/log/pixel-source-broker/sync.log',
])
def test_added_code_writer_cannot_address_source_private_families(path):
    with pytest.raises(access.InstallError, match='code-image-invalid'):
        access._source_added_write(path, expected=None, replacement=b'not authority', mode=0o644, gid=0)


@pytest.mark.parametrize('fault', [None, 'unknown-file', 'changed-image', 'hardlink'])
def test_fixed_code_callback_uses_real_no_follow_atomic_cas_and_preserves_other_files(tmp_path, monkeypatch, fault):
    """Only Root ownership/Directory ACL are simulated; all file IO is actual."""
    import pixel_macos_custody as custody
    target = tmp_path / 'candidate.py'
    token = tmp_path / 'unrelated-private-token'
    token.write_bytes(b'INVENTED_CREDENTIAL_NEVER_ADDRESSED')
    stamp = token.stat()
    monkeypatch.setattr(access._upgrade, 'SOURCE_ADDITIONS', frozenset((str(target),)))
    monkeypatch.setattr(access, '_mkdir_deployment_directories', lambda path: path.mkdir(exist_ok=True))
    @contextmanager
    def directory(path):
        assert Path(path) == tmp_path
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: yield fd
        finally: os.close(fd)
    def root_file(fd, *, directory):
        info = os.fstat(fd)
        if directory or info.st_nlink != 1:
            raise access.InstallError('owned fixture metadata refuses unsafe file')
        fields = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
        fields.update(st_uid=0, st_gid=0)
        return SimpleNamespace(**fields)
    monkeypatch.setattr(custody, 'protected_directory', directory)
    monkeypatch.setattr(custody, '_verify_fd', root_file)
    monkeypatch.setattr(access.os, 'fchown', lambda _fd, uid, gid: (uid, gid) == (0, 0)
        or pytest.fail('only Root code ownership may be requested'))
    body = b'candidate code image, not broker data'
    if fault == 'unknown-file': target.write_bytes(b'preserve unknown image')
    if fault == 'unknown-file':
        with pytest.raises(access.InstallError):
            access._source_added_write(target, expected=None, replacement=body, mode=0o644, gid=0)
        assert target.read_bytes() == b'preserve unknown image'
    else:
        access._source_added_write(target, expected=None, replacement=body, mode=0o644, gid=0)
        assert target.read_bytes() == body and target.stat().st_mode & 0o777 == 0o644
        if fault == 'changed-image': target.write_bytes(b'changed candidate must survive')
        if fault == 'hardlink': os.link(target, tmp_path / 'unexpected-link')
        if fault:
            with pytest.raises(access.InstallError): access._source_added_remove(target, expected=body, mode=0o644, gid=0)
            assert target.exists()
        else:
            access._source_added_remove(target, expected=body, mode=0o644, gid=0)
            assert not target.exists()
    assert (token.stat().st_dev, token.stat().st_ino) == (stamp.st_dev, stamp.st_ino)
    assert token.read_bytes() == b'INVENTED_CREDENTIAL_NEVER_ADDRESSED'
