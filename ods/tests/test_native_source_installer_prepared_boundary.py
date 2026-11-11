"""Actual installer journal ordering; no macOS execution or credential access."""
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_native_source_installer_recovery import (access, upgrade, source_images,
    context_plan, binding, withdrawal_authority, selection, owned_images, CURRENT, CANDIDATE)


@pytest.mark.parametrize('mode', ['absent', 'withdrawal'])
@pytest.mark.parametrize('fault', [None, 'context-drift', 'family-drift', 'proof-refusal',
    'withdrawal-refusal', 'fresh-token-drift'])
def test_prepared_installer_uses_actual_context_and_receipt_before_code_or_data_changes(tmp_path, monkeypatch, mode, fault):
    import pixel_access_bridge as bridge_module
    import pixel_macos_custody as mac_custody
    from pixel_connections import custody, native_executor
    records = source_images()
    plan = context_plan(records)
    plan['source_lifecycle']['mode'] = mode
    raw = (json.dumps(access._recovery_context(plan), sort_keys=True) + '\n').encode()
    context = tmp_path / ('runtime-upgrade-context-' + CANDIDATE + '.json')
    context.write_bytes(raw)
    transaction = dict(kind='native-installer-upgrade', transaction=hashlib.sha256(raw).hexdigest(),
        currentDigest=CURRENT, candidateDigest=CANDIDATE)
    private = tmp_path / 'unaddressed-google-token'
    private.write_bytes(b'INVENTED_PRIVATE_TOKEN_NOT_READ_BY_INSTALLER')
    original_token = private.stat()
    bridge = SimpleNamespace(state=tmp_path)
    backend = SimpleNamespace(bridge=bridge, selection=object())
    events = []
    original_read = bridge_module.private_json
    monkeypatch.setattr(bridge_module, 'private_json', lambda p, _uid, maximum:
        original_read(p, os.geteuid(), maximum))
    monkeypatch.setattr(mac_custody, 'protected_bytes', lambda path, *, limit: Path(path).read_bytes())
    addition, receipt = withdrawal_authority(records)
    proof = addition['proof'] if mode == 'withdrawal' else binding(records)['proof']
    if mode == 'withdrawal':
        proof['transaction'] = copy.deepcopy(transaction)
        receipt['transaction'] = copy.deepcopy(transaction)
        receipt['intentSha256'] = hashlib.sha256((json.dumps(proof, sort_keys=True,
            separators=(',', ':'), allow_nan=False) + '\n').encode()).hexdigest()
    else:
        proof['transaction'] = transaction['transaction']
    def prove(*_a, **kwargs):
        events.append('proof')
        assert kwargs['transaction'] == transaction
        assert not (tmp_path / 'runtime-upgrade.json').exists()
        if fault == 'proof-refusal': raise ValueError('fixture refuses unqualified state')
        return proof
    def quiesce(): events.append('all-six-roles-quiesced')
    backend.quiesce_for_sign_in = quiesce
    def withdraw(selected, *, transaction, intent):
        events.append('withdrawal')
        assert selected is backend.selection and intent == proof
        actual = original_read(tmp_path / 'runtime-upgrade.json', os.geteuid(), upgrade.JOURNAL_LIMIT)
        assert actual['phase'] == 'prepared' and actual['sourceAddition']['proof'] == proof
        assert 'receipt' not in actual['sourceAddition']
        if fault == 'withdrawal-refusal': raise ValueError('fixture preserves partial withdrawal')
        return receipt
    def fresh(*_a, **kwargs):
        events.append('fresh')
        assert kwargs['transaction'] == transaction
        if fault == 'fresh-token-drift': raise ValueError('fixture refuses changed token metadata')
        if mode == 'withdrawal': assert kwargs['intent'] == proof and kwargs['receipt'] == receipt
    monkeypatch.setattr(custody, 'native_absent_for_installer', prove)
    monkeypatch.setattr(custody, 'native_verify_installer_absence', fresh)
    publisher = SimpleNamespace(installer_withdrawal_intent=prove,
        withdraw_installer_generation=withdraw, verify_installer_withdrawal=fresh)
    monkeypatch.setattr(native_executor, '_publisher', lambda _s: publisher)
    if fault == 'context-drift': context.write_bytes(raw + b' ')
    if fault == 'family-drift': records[0]['after'] += b'changed candidate code'
    expected_failure = fault is not None and not (mode == 'absent' and fault == 'withdrawal-refusal')
    def prepare():
        return access._prepare_source_upgrade_journal(plan, bridge, records, context_raw=raw,
            backend=backend if mode == 'withdrawal' else None)
    if expected_failure:
        with pytest.raises((access.InstallError, upgrade.UpgradeError, ValueError)): prepare()
    else:
        journal = prepare()
        assert journal.value['schemaVersion'] == 4 and journal.value['phase'] == 'prepared'
        assert upgrade.decode_recovery(journal.value, **selection(records)) == records
        if mode == 'withdrawal':
            assert journal.value['sourceAddition']['receipt'] == receipt
            assert events[:3] == ['all-six-roles-quiesced', 'proof', 'withdrawal']
            assert events.count('fresh') >= 2  # Prospective receipt and committed CAS.
        else:
            assert 'receipt' not in journal.value['sourceAddition']
            assert events == ['proof', 'fresh']
    if fault in ('context-drift', 'family-drift'):
        assert not events and not (tmp_path / 'runtime-upgrade.json').exists()
    elif fault == 'proof-refusal':
        assert not (tmp_path / 'runtime-upgrade.json').exists()
    elif expected_failure:
        actual = original_read(tmp_path / 'runtime-upgrade.json', os.geteuid(), upgrade.JOURNAL_LIMIT)
        assert actual['phase'] == 'prepared' and 'receipt' not in actual['sourceAddition']
    assert (private.stat().st_dev, private.stat().st_ino, private.stat().st_size,
        private.stat().st_mtime_ns) == (original_token.st_dev, original_token.st_ino,
            original_token.st_size, original_token.st_mtime_ns)
    # The installer addresses only its context and journal; no Source code or
    # private family is created by preparing this boundary.
    assert {p.name for p in tmp_path.iterdir()} <= {context.name, private.name, 'runtime-upgrade.json'}


def test_legacy_unsupported_active_publisher_is_refused_before_context_publication(monkeypatch):
    from pixel_connections import custody, native_executor, source_backend
    records = source_images()
    plan = context_plan(records)
    plan['source_lifecycle']['mode'] = 'withdrawal'
    bridge, selected = object(), object()
    calls = []
    monkeypatch.setattr(source_backend, 'for_bridge', lambda *_a, **_kw: SimpleNamespace(selection=selected))
    monkeypatch.setattr(custody, 'native_verify_kit', lambda *_a, **_kw: calls.append('actual-old-kit'))
    monkeypatch.setattr(native_executor, '_publisher', lambda _s: SimpleNamespace())
    with pytest.raises(access.InstallError, match='withdrawal-capability-required'):
        access._source_upgrade_preflight(plan, bridge)
    assert calls == ['actual-old-kit']


def test_legacy_data_absent_preflight_uses_explicit_custody_path_without_publisher(monkeypatch):
    from pixel_connections import custody, native_executor
    plan = context_plan(source_images())
    bridge = object()
    calls = []
    def inspect(actual, **kwargs):
        calls.append((actual, kwargs))
        return {'state': (None, {}, None, {})}
    monkeypatch.setattr(custody, 'native_inspect_installer_absence', inspect)
    monkeypatch.setattr(native_executor, '_publisher', lambda _s: pytest.fail('no invented legacy Source capability'))
    assert access._source_upgrade_preflight(plan, bridge) is None
    assert calls == [(bridge, {'current_digest': CURRENT})]


@pytest.mark.parametrize('mode', ['absent', 'withdrawal'])
def test_installer_file_adapter_preserves_fresh_custody_on_each_mixed_image_and_rollback(tmp_path, monkeypatch, mode):
    from pixel_connections import custody
    records, files, mutations, read, create, remove, generic = owned_images(tmp_path)
    plan = context_plan(records)
    plan['source_lifecycle']['mode'] = mode
    if mode == 'withdrawal':
        addition, receipt = withdrawal_authority(records)
        addition['receipt'] = receipt
    else:
        addition = binding(records)
    journal = SimpleNamespace(value=upgrade.encode_recovery(records, source_addition=addition, **selection(records)))
    bridge = object()
    observed = []
    refusal = []
    def fresh(actual, proof, *, transaction):
        assert actual is bridge
        assert proof == addition['receipt' if mode == 'withdrawal' else 'proof']
        assert transaction == dict(kind='native-installer-upgrade', transaction=addition['contextSha256'],
            currentDigest=CURRENT, candidateDigest=CANDIDATE)
        observed.append(len(mutations))
        if refusal: raise ValueError('fresh actor census refuses deletion')
    monkeypatch.setattr(custody, 'native_verify_installer_absence', fresh)
    monkeypatch.setattr(custody, 'native_verify_installer_withdrawal', fresh)
    monkeypatch.setattr(access, '_source_added_write', create)
    monkeypatch.setattr(access, '_source_added_remove', remove)
    apply_options, rollback_options = access._source_upgrade_file_options(plan, records, journal, bridge)
    upgrade.replace_deployment_files(records, read=read, replace=generic, **apply_options)
    assert observed == list(range(len(records)))
    refusal.append(True)
    with pytest.raises(ValueError, match='actor census'):
        upgrade.restore_deployment_files(records, read=read, replace=generic, **rollback_options)
    assert len(mutations) == len(records) and all(p.exists() for p in files.values())
    refusal.clear()
    upgrade.restore_deployment_files(records, read=read, replace=generic, **rollback_options)
    assert not any(p.exists() for p in files.values())


def test_actual_file_adapter_rejects_withdrawal_intent_without_completed_receipt():
    records = source_images()
    plan = context_plan(records)
    plan['source_lifecycle']['mode'] = 'withdrawal'
    addition, _receipt = withdrawal_authority(records)
    journal = SimpleNamespace(value=upgrade.encode_recovery(records, source_addition=addition, **selection(records)))
    with pytest.raises(access.InstallError, match='complete-withdrawal-required'):
        access._source_upgrade_file_options(plan, records, journal, object())


@pytest.mark.parametrize('phase', ['active', 'restored'])
@pytest.mark.parametrize('fault', [None, 'settings-drift', 'wrong-target', 'environment-drift',
    'wrong-transaction', 'active-result', 'unconfirmed-kit'])
def test_final_installer_boundary_binds_actual_started_target_before_finishing(tmp_path, monkeypatch, phase, fault):
    from pixel_connections import custody, native_executor
    import pixel_macos_custody as mac_custody
    records = source_images()
    plan = context_plan(records)
    plan['source_lifecycle']['mode'] = 'withdrawal'
    addition, receipt = withdrawal_authority(records)
    addition['receipt'] = receipt
    settings = tmp_path / 'actual-installed-root-settings.json'
    before = json.dumps({'owner': plan['owner'].pw_name, 'version': 'old'}).encode()
    after = json.dumps({'owner': plan['owner'].pw_name, 'version': 'new'}).encode()
    side = 'after' if phase == 'active' else 'before'
    settings.write_bytes(after if phase == 'active' else before)
    records.append(dict(path=str(settings), before=before, after=after, mode=0o600, gid=0))
    journal = SimpleNamespace(value=dict(schemaVersion=4, phase=phase, sourceAddition=addition))
    monkeypatch.setitem(access.ACCESS_FILES, 'config', settings)
    monkeypatch.setattr(mac_custody, 'protected_bytes', lambda path, **_kw: Path(path).read_bytes())
    events = []
    @contextmanager
    def bounded(seconds):
        assert seconds == access.SOURCE_FINISH_SECONDS
        events.append('bounded-target-bridge')
        yield
    bridge = SimpleNamespace(bounded=bounded, connection_deployment=lambda: 'actual-target-deployment')
    def discover(actual):
        assert actual is bridge
        events.append('discover-current-target')
    monkeypatch.setattr(access, '_discover_source_installer_bridge', discover)
    def actual_bridge(selected_plan, *, owner_name):
        assert selected_plan['access_settings'] == json.loads(after if side == 'after' else before)
        assert owner_name == plan['owner'].pw_name
        return bridge
    monkeypatch.setattr(access, '_bridge_from_recovery_plan', actual_bridge)
    selected = object()
    monkeypatch.setattr(custody, 'native_source_selection', lambda actual, deployed:
        selected if actual is bridge and deployed == 'actual-target-deployment' else pytest.fail('wrong target bridge'))
    expected = CANDIDATE if phase == 'active' else CURRENT
    monkeypatch.setattr(custody, '_native_selected', lambda value, **_kw:
        {'bundleSha256': 'f' * 64 if fault == 'wrong-target' else expected})
    def rebind(value, exact_receipt, *, transaction):
        events.append('typed-target-rebind')
        assert value is selected and exact_receipt == receipt
        assert transaction == dict(kind='native-installer-upgrade', transaction=addition['contextSha256'],
            currentDigest=CURRENT, candidateDigest=CANDIDATE)
        result = dict(status='inactive', generation='d' * 32, environment=copy.deepcopy(receipt['environment']),
            transaction=copy.deepcopy(transaction), kitSha256='1' * 64)
        if fault == 'environment-drift': result['environment']['inode'] = [1, 777]
        if fault == 'wrong-transaction': result['transaction']['transaction'] = 'e' * 64
        if fault == 'active-result': result['status'] = 'active'
        return result
    monkeypatch.setattr(custody, 'native_rebind_withdrawn_generation', rebind)
    def verify(value, *, transaction, receipt):
        events.append('independent-kit-proof')
        assert value is selected
        return dict(status='inactive', generation='d' * 32, environment=copy.deepcopy(receipt['environment']),
            transaction=copy.deepcopy(transaction), kitSha256='2' * 64 if fault == 'unconfirmed-kit' else '1' * 64)
    monkeypatch.setattr(native_executor, '_publisher', lambda _s: SimpleNamespace(verify_installer_rebind_generation=verify))
    if fault == 'settings-drift': settings.write_bytes(b'unknown settings are preserved')
    original = settings.read_bytes()
    if fault:
        with pytest.raises(access.InstallError): access._finish_source_upgrade(plan, records, journal)
    else:
        access._finish_source_upgrade(plan, records, journal)
        assert events == ['bounded-target-bridge', 'discover-current-target', 'typed-target-rebind', 'independent-kit-proof']
    if fault == 'settings-drift': assert not events
    if fault == 'wrong-target': assert 'typed-target-rebind' not in events
    assert settings.read_bytes() == original
    assert journal.value['phase'] == phase  # This proof cannot finish the installer itself.


@pytest.mark.parametrize('phase', ['held', 'releasing'])
@pytest.mark.parametrize('fault', [None, 'source-refusal', 'source-journal-drift'])
def test_source_completed_proof_runs_only_under_fresh_hold_and_preserves_generic_release_proof(tmp_path, monkeypatch, phase, fault):
    import pixel_access_bridge as bridge
    path = tmp_path / 'actual-edge-journal.json'
    record = dict(schemaVersion=1, container='a' * 64, binding={'token': 'b' * 64, 'revision': 'c' * 64},
        phase=phase, status={'admission_blocked': True, 'streams': 0})
    bridge.atomic_json(path, record)
    read = bridge.private_json
    monkeypatch.setattr(bridge, 'private_json', lambda p, _uid, limit: read(p, os.geteuid(), limit))
    monkeypatch.setattr(access, '_edge_hold_journal', lambda _p: path)
    events = []
    def rpc(_p, _cid, operation, binding):
        assert binding == record['binding']
        events.append(operation)
        if operation == 'acquire': return record['status']
        assert operation == 'release'
        return dict(capability='available', phase='idle', streams=0, admission_blocked=False)
    monkeypatch.setattr(access, '_migration_edge_request', rpc)
    def source():
        events.append('source-proof')
        assert read(path, os.geteuid(), 65536)['phase'] == 'held'
        if fault == 'source-refusal': raise ValueError('fresh Source completion proof refuses')
        if fault == 'source-journal-drift': bridge.atomic_json(path, dict(record, container='f' * 64))
    def generic(): events.append('generic-controller-proof')
    if phase == 'held' and fault:
        with pytest.raises((access.InstallError, ValueError)):
            access._finish_migration_hold({}, record, verify_before_release=generic, verify_while_held=source)
        assert 'release' not in events and 'generic-controller-proof' not in events
        assert read(path, os.geteuid(), 65536)['phase'] == 'held'
    else:
        access._finish_migration_hold({}, record, verify_before_release=generic, verify_while_held=source)
        assert read(path, os.geteuid(), 65536)['phase'] == 'released'
        assert events == (['acquire', 'source-proof'] if phase == 'held' else []) + ['generic-controller-proof', 'release']


def current_owner_snapshot():
    bodies = {'gateway.env': 'PIXEL_EMAIL_INBOX=1\n', 'onboarding.json': '{"emailInbox":true}\n'}
    config = b'actual admitted old config; distinct from staging config bytes'
    runtime = dict(deploymentSha256='d' * 64, custodySha256='e' * 64, bundleSha256=CURRENT, sourceRef='f' * 40)
    snapshot = dict(schemaVersion=1, kind='ods-native-source-installer-sidefiles', owner={'uid': 501, 'gid': 20},
        runtime=runtime, config={'path': '/private/fixed/current-live-config.json', 'inode': [1, 20],
            'sha256': hashlib.sha256(config).hexdigest(), 'size': len(config)}, parent=[1, 21],
        files={name: {'inode': [1, 22 + i], 'sha256': hashlib.sha256(text.encode()).hexdigest(), 'size': len(text.encode())}
            for i, (name, text) in enumerate(bodies.items())}, record={'inode': [1, 24], 'sha256': '1' * 64},
        enrollment=None, proof=dict(revision='2' * 64, configSha256=hashlib.sha256(config).hexdigest(),
            configuredMode='sandboxed', effectiveMode='sandboxed'), bootstrap={'inode': [1, 25], 'sha256': '3' * 64})
    return snapshot, bodies


@pytest.mark.parametrize('live_hold', [True, False])
def test_started_source_recovery_requires_live_sdk_hold_before_publisher(tmp_path, monkeypatch, live_hold):
    """A valid durable held receipt cannot substitute for the live acquire."""
    import pixel_access_bridge as bridge_module
    path = tmp_path / 'edge.json'
    held = dict(capability='available', phase='held', streams=0, admission_blocked=True, revision='c' * 64)
    record = dict(schemaVersion=1, container='a' * 64,
        binding={'token': 'b' * 64, 'revision': 'c' * 64}, phase='held', status=held)
    bridge_module.atomic_json(path, record)
    original = path.read_bytes()
    read = bridge_module.private_json
    monkeypatch.setattr(bridge_module, 'private_json', lambda p, _uid, limit: read(p, os.geteuid(), limit))
    monkeypatch.setattr(access, '_edge_hold_journal', lambda _plan: path)
    monkeypatch.setattr(access, '_migration_edge_context', lambda _plan: {})
    monkeypatch.setattr(access.subprocess, 'run', lambda *_args, **_kwargs:
        SimpleNamespace(stdout='a' * 64 + ' true pixel-edge'))
    events = []
    def rpc(_plan, container, operation, binding):
        assert container == record['container'] and binding == record['binding']
        events.append('live-' + operation)
        if operation == 'acquire' and live_hold:
            return held
        return dict(capability='available', phase='idle', streams=0,
            admission_blocked=False, revision='c' * 64)
    monkeypatch.setattr(access, '_migration_edge_request', rpc)
    services = {role: object() for role in ('gateway', 'access', 'relay')}
    monkeypatch.setattr(access, '_upgrade_services', lambda *_args: ({}, services))
    for name in ('_verify_bundle_selection', '_upgrade_service_identity', '_candidate_upgrade_policy_mode',
                 '_ready_gateway', '_ready_access', '_ready_access_relay', '_clear_candidate_stop_witnesses'):
        monkeypatch.setattr(access, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(access, '_upgrade_policy_mode', lambda *_args: pytest.fail('active recovery checked the previous controller'))
    monkeypatch.setattr(access, '_finish_source_upgrade', lambda *_args: events.append('source-publication'))
    def finish(verify):
        verify()
        events.append('journal-finish')
    journal = SimpleNamespace(value={'phase': 'active'}, finish=finish)
    plan = {'source_lifecycle': {'mode': 'absent'}, 'access_settings': {'gateway_port': 18789}}
    if live_hold:
        access._resume_source_upgrade_active(plan, [], journal, verify_snapshots=lambda: None)
        assert events == ['live-acquire', 'source-publication', 'journal-finish', 'live-acquire', 'live-release']
        assert read(path, os.geteuid(), 65536)['phase'] == 'released'
    else:
        with pytest.raises(access.InstallError, match='edge-hold-unconfirmed'):
            access._resume_source_upgrade_active(plan, [], journal, verify_snapshots=lambda: None)
        assert events == ['live-acquire'] and path.read_bytes() == original


@pytest.mark.parametrize('capability', ['missing', 'refuses'])
def test_prior_installer_history_cannot_skip_rollover_before_withdrawal(tmp_path, monkeypatch, capability):
    import pixel_access_bridge as bridge_module
    import pixel_macos_custody as mac_custody
    from pixel_connections import native_executor
    records = source_images()
    plan = context_plan(records)
    plan['source_lifecycle']['mode'] = 'withdrawal'
    raw = (json.dumps(access._recovery_context(plan), sort_keys=True) + '\n').encode()
    (tmp_path / ('runtime-upgrade-context-' + CANDIDATE + '.json')).write_bytes(raw)
    transaction = dict(kind='native-installer-upgrade', transaction=hashlib.sha256(raw).hexdigest(),
        currentDigest=CURRENT, candidateDigest=CANDIDATE)
    addition, _receipt = withdrawal_authority(records)
    proof = addition['proof']
    proof['transaction'] = transaction
    history = tmp_path / 'source-installer-rebind.json'
    history.write_bytes(b'invented prior history; only the protected publisher may qualify it')
    before = history.read_bytes(), history.stat().st_ino
    bridge = SimpleNamespace(state=tmp_path)
    backend = SimpleNamespace(bridge=bridge, selection=object(), quiesce_for_sign_in=lambda: None)
    read = bridge_module.private_json
    monkeypatch.setattr(bridge_module, 'private_json', lambda p, _uid, limit: read(p, os.geteuid(), limit))
    monkeypatch.setattr(mac_custody, 'protected_bytes', lambda path, *, limit: Path(path).read_bytes())
    def forbidden(*_args, **_kwargs):
        pytest.fail('prior history must be retained before withdrawal or Source code changes')
    publisher = SimpleNamespace(installer_withdrawal_intent=lambda *_args, **_kwargs: proof,
        withdraw_installer_generation=forbidden)
    if capability == 'refuses':
        def rollover(selected, *, transaction):
            assert selected is backend.selection
            saved = read(tmp_path / 'runtime-upgrade.json', os.geteuid(), upgrade.JOURNAL_LIMIT)
            assert saved['phase'] == 'prepared' and 'receipt' not in saved['sourceAddition']
            raise ValueError('qualified publisher refuses prior history')
        publisher.rollover_installer_history = rollover
    monkeypatch.setattr(native_executor, '_publisher', lambda selected: publisher)
    expected = 'history-capability-required' if capability == 'missing' else 'publisher refuses prior history'
    with pytest.raises((access.InstallError, ValueError), match=expected):
        access._prepare_source_upgrade_journal(plan, bridge, records, context_raw=raw, backend=backend)
    saved = read(tmp_path / 'runtime-upgrade.json', os.geteuid(), upgrade.JOURNAL_LIMIT)
    assert saved['phase'] == 'prepared' and 'receipt' not in saved['sourceAddition']
    assert (history.read_bytes(), history.stat().st_ino) == before


@pytest.mark.parametrize('fault', [None, 'publisher', 'fresh-proof'])
def test_prepared_withdrawal_recovery_uses_actual_typed_receipt_cas(tmp_path, monkeypatch, fault):
    import pixel_access_bridge as bridge_module
    from pixel_connections import custody, native_executor, source_backend
    records = source_images()
    plan = context_plan(records)
    plan['source_lifecycle']['mode'] = 'withdrawal'
    addition, receipt = withdrawal_authority(records)
    transaction = dict(kind='native-installer-upgrade', transaction=addition['contextSha256'],
        currentDigest=CURRENT, candidateDigest=CANDIDATE)
    options = dict(current_digest=CURRENT, candidate_digest=CANDIDATE,
        allowed_paths={item['path'] for item in records})
    value = upgrade.encode_recovery(records, source_addition=addition, **options)
    read = bridge_module.private_json
    monkeypatch.setattr(bridge_module, 'private_json', lambda p, _uid, limit: read(p, os.geteuid(), limit))
    journal = upgrade.RecoveryJournal.create(tmp_path / 'runtime-upgrade.json', value)
    before = journal.path.read_bytes()
    bridge, selected = object(), object()
    events = []
    backend = SimpleNamespace(selection=selected, quiesce_for_sign_in=lambda: events.append('quiesce'))
    monkeypatch.setattr(source_backend, 'for_bridge', lambda actual, **_kwargs:
        backend if actual is bridge else pytest.fail('wrong actual bridge'))
    def withdraw(actual, **kwargs):
        assert actual is selected and kwargs == {'transaction': transaction, 'intent': addition['proof']}
        assert read(journal.path, os.geteuid(), upgrade.JOURNAL_LIMIT)['phase'] == 'prepared'
        events.append('withdraw')
        if fault == 'publisher': raise ValueError('exact old publisher refuses')
        return receipt
    publisher = SimpleNamespace(withdraw_installer_generation=withdraw,
        verify_installer_withdrawal=lambda *_args, **_kwargs: receipt)
    monkeypatch.setattr(native_executor, '_publisher', lambda actual: publisher)
    def fresh(actual, actual_receipt, **kwargs):
        assert actual is bridge and actual_receipt == receipt and kwargs == {'transaction': transaction}
        events.append('fresh')
        if fault == 'fresh-proof': raise ValueError('fresh Root custody refuses')
    monkeypatch.setattr(custody, 'native_verify_installer_withdrawal', fresh)
    if fault:
        with pytest.raises(ValueError):
            access._complete_prepared_source_withdrawal(plan, records, journal, bridge, transaction)
        assert journal.path.read_bytes() == before and 'receipt' not in journal.value['sourceAddition']
    else:
        access._complete_prepared_source_withdrawal(plan, records, journal, bridge, transaction)
        saved = read(journal.path, os.geteuid(), upgrade.JOURNAL_LIMIT)
        assert saved['phase'] == 'prepared' and saved['sourceAddition']['receipt'] == receipt
        assert events[:2] == ['quiesce', 'withdraw'] and events.count('fresh') >= 2


@pytest.mark.parametrize('phase', ['prepared', 'rolling-back'])
def test_recovery_rolls_history_only_before_prepared_source_file_guards(monkeypatch, phase):
    records = source_images()
    plan = context_plan(records)
    addition = binding(records)
    journal = SimpleNamespace(value={'schemaVersion': 4, 'phase': phase, 'sourceAddition': addition})
    bridge = object()
    events = []
    def discover(actual):
        assert actual is bridge
        events.append('discover-current-before-stop')
    monkeypatch.setattr(access, '_discover_source_installer_bridge', discover)
    def rollover(actual, transaction):
        assert actual is bridge and transaction == dict(kind='native-installer-upgrade',
            transaction=addition['contextSha256'], currentDigest=CURRENT, candidateDigest=CANDIDATE)
        events.append('rollover')
    monkeypatch.setattr(access, '_rollover_source_installer_history', rollover)
    monkeypatch.setattr(access, '_source_upgrade_file_options', lambda *_args:
        (events.append('fresh-file-guards') or {}, {}))
    monkeypatch.setattr(access, '_upgrade_services', lambda *_args: ({}, {}))
    monkeypatch.setattr(access, '_recover_unstarted_upgrade', lambda *_args, **_kwargs:
        events.append('verified-unstarted-recovery') or True)
    access._recover_upgrade(plan, records, journal, verify_snapshots=lambda: None, source_bridge=bridge)
    assert events == (['discover-current-before-stop', 'rollover'] if phase == 'prepared' else []) + ['fresh-file-guards', 'verified-unstarted-recovery']


@pytest.mark.parametrize('fault', [None, 'body-reset', 'owner', 'target-relabel', 'mode-proof', 'unknown', 'missing-intent'])
def test_context_preserves_current_admitted_sidefiles_and_does_not_confuse_old_with_staged_config(monkeypatch, fault):
    plan = context_plan(source_images())
    snapshot, bodies = current_owner_snapshot()
    plan['runtime_bundle'].update(source_sidefiles_before=snapshot, connection_sidefiles=bodies)
    if fault == 'body-reset': bodies['gateway.env'] = 'PIXEL_EMAIL_INBOX=0\n'
    if fault == 'owner': snapshot['owner']['uid'] = 502
    if fault == 'target-relabel': snapshot['runtime']['bundleSha256'] = CANDIDATE
    if fault == 'mode-proof': snapshot['proof']['effectiveMode'] = 'full-access'
    if fault == 'unknown': snapshot['acceptArbitraryOwnerFiles'] = True
    if fault == 'missing-intent': plan.pop('source_lifecycle')
    monkeypatch.setattr(access.pwd, 'getpwnam', lambda _name: plan['owner'])
    if fault:
        with pytest.raises(access.InstallError): access._recovery_context(plan)
    else:
        saved = access._recovery_context(plan)
        loaded = access._decode_recovery_context(saved, current_digest=CURRENT, candidate_digest=CANDIDATE,
            owner_name=plan['owner'].pw_name)
        assert loaded == plan
        assert loaded['runtime_bundle']['connection_sidefiles'] == bodies
        # Current admitted config bytes/path may differ from candidate staging.
        assert snapshot['config']['sha256'] != hashlib.sha256(plan['runtime_bundle']['source_config_bytes']).hexdigest()


@pytest.mark.parametrize('mode', ['absent', 'withdrawal'])
def test_actual_old_selection_capture_replaces_bootstrap_defaults_before_context(monkeypatch, mode):
    from pixel_connections import custody, native_executor, source_backend
    plan = context_plan(source_images())
    plan['source_lifecycle']['mode'] = mode
    plan['runtime_bundle']['connection_sidefiles'] = {'gateway.env': 'bootstrap disabled', 'onboarding.json': '{}'}
    snapshot, bodies = current_owner_snapshot()
    selected = SimpleNamespace(deployment=SimpleNamespace(checksum=snapshot['runtime']['deploymentSha256']))
    backend = SimpleNamespace(selection=selected)
    monkeypatch.setattr(source_backend, 'for_bridge', lambda *_a, **_kw: backend)
    actual_bridge = SimpleNamespace(connection_deployment=lambda: selected.deployment)
    inspections = []
    def inspect(bridge, **kwargs):
        assert bridge is actual_bridge and kwargs == {'current_digest': CURRENT}
        inspections.append(True)
        return {'state': ({'inode': [1, 30], 'files': {}}, {}, None, {})}
    monkeypatch.setattr(custody, 'native_inspect_installer_absence', inspect)
    monkeypatch.setattr(custody, 'native_source_selection', lambda bridge, deployment:
        selected if bridge is actual_bridge and deployment is selected.deployment else pytest.fail('wrong old selection'))
    monkeypatch.setattr(custody, 'native_verify_kit', lambda *_a, **_kw: None)
    def forbidden(*_a, **_kw): pytest.fail('preflight cannot execute any Source mutation')
    publisher = SimpleNamespace(**{name: forbidden for name in ('installer_withdrawal_intent',
        'withdraw_installer_generation', 'verify_installer_withdrawal', 'rebind_installer_generation',
        'verify_installer_rebind_generation')})
    publisher._material = lambda actual: (custody, dict(runtimeCustodySha256=snapshot['runtime']['custodySha256'],
        bundleSha256=CURRENT, configPath=snapshot['config']['path']), {}, snapshot['runtime']['sourceRef'])
    monkeypatch.setattr(native_executor, '_publisher', lambda actual: publisher if actual is selected else pytest.fail('wrong selection'))
    monkeypatch.setattr(custody, 'native_installer_sidefiles_snapshot', lambda actual:
        {'snapshot': snapshot, 'sidefiles': bodies} if actual is selected else pytest.fail('wrong capture'))
    assert access._source_upgrade_preflight(plan, actual_bridge) is (backend if mode == 'withdrawal' else None)
    assert len(inspections) == (2 if mode == 'absent' else 0)
    assert plan['runtime_bundle']['source_sidefiles_before'] == snapshot
    assert plan['runtime_bundle']['connection_sidefiles'] == bodies
    assert 'emailInbox' in plan['runtime_bundle']['connection_sidefiles']['onboarding.json']


@pytest.mark.parametrize('fault', [None, 'wrong-phase', 'existing-kit', 'fresh-refusal'])
def test_first_owner_sidefile_producer_is_bounded_by_typed_absence_before_gateway_start(monkeypatch, fault):
    from pixel_connections import custody
    records = source_images()
    plan = context_plan(records)
    addition = binding(records)
    addition['proof']['kit'] = None
    if fault == 'existing-kit': addition['proof']['kit'] = {'unexpected': 'old kit requires typed migration'}
    journal = SimpleNamespace(value=dict(schemaVersion=4, phase='active' if fault == 'wrong-phase' else 'replacing-files',
        sourceAddition=addition))
    actual_bridge = object()
    events = []
    def fresh(bridge, proof, *, transaction):
        assert bridge is actual_bridge and proof == addition['proof']
        events.append('fresh')
        if fault == 'fresh-refusal': raise ValueError('new Source actor or private data preserved')
    monkeypatch.setattr(custody, 'native_verify_installer_absence', fresh)
    monkeypatch.setattr(access, '_connection_sidefiles', lambda actual:
        events.append('existing-inode-journal-producer') if actual is plan else pytest.fail('wrong declaration'))
    if fault:
        with pytest.raises((access.InstallError, ValueError)):
            access._prepare_source_upgrade_sidefiles(plan, records, journal, actual_bridge)
        assert 'existing-inode-journal-producer' not in events
    else:
        access._prepare_source_upgrade_sidefiles(plan, records, journal, actual_bridge)
        assert events == ['fresh', 'existing-inode-journal-producer', 'fresh']
