"""Real Root files and owned Linux children; explicit Darwin/launchd simulation.

This qualifies publication/recovery only, never a physical Mac or provider effect.
"""
# ruff: noqa: E402,F401,F811
import json
import os
from pathlib import Path
import sys

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs metadata harness, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_executor import executor, qualification_operation
from test_native_source_generation import generation, SPECS
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from test_native_source_runtime_publication import built, source, package
from test_native_source_publication import material, publication, helper
from pixel_connections import custody as c, native_executor as ex


def journal(n):
    return json.loads((n.metadata / 'source-environment-generation.json').read_bytes())


def target(n, calendar='another-calendar'):
    return c.native_environment_text(n.selected, {'PIXEL_SOURCE_CALENDAR_ID': calendar}, _publication=True)


def files(n):
    return {str(path.relative_to(n.world)): path.read_bytes()
        for path in n.world.rglob('*') if path.is_file() and not path.is_symlink()}


def stopped(n):
    job = n.executor.start('sync')
    result = n.executor.stop('sync')
    assert result['phase'] == 'stopped' and result['uncertain'] is True
    assert all(process.poll() is not None for process in n.driver.children)
    return job


def test_real_child_stopped_authority_withdraws_before_environment_and_new_inactive_slots(executor, monkeypatch):
    n = executor
    job = stopped(n)
    old_gate = (c.NATIVE_STATE / 'custody.json').read_bytes()
    old_execution = (n.metadata / 'source-execution.json').read_bytes()
    old_definitions = {path.name: (path.read_bytes(), path.stat().st_ino) for path in n.units.iterdir()}
    original = c.native_publish_environment
    calls = []
    def publish(selection, text):
        record = journal(n)
        archive = n.metadata / ('.source-environment-' + record['generation'])
        assert (archive / 'custody.before.json').read_bytes() == old_gate
        assert json.loads((c.NATIVE_STATE / 'custody.json').read_bytes())['schemaVersion'] == 1
        assert not list(n.units.iterdir()) and not (n.metadata / 'native-source-jobs').exists()
        assert (archive / 'source-execution.json').read_bytes() == old_execution
        calls.append(True)
        return original(selection, text)
    monkeypatch.setattr(c, 'native_publish_environment', publish)
    result = helper.publish_environment_generation(n.selected, target(n))
    record = journal(n)
    archive = n.metadata / ('.source-environment-' + result['generation'])
    assert calls == [True] and result['status'] == 'inactive'
    assert result['previousGeneration'] == job['invocation']['generation'] != result['generation']
    for filename, (body, inode) in old_definitions.items():
        assert (archive / filename).read_bytes() == body and (archive / filename).stat().st_ino == inode
    assert json.loads((archive / 'source-execution.json').read_bytes())['jobs'][c.NATIVE_LABEL + 'sync']['uncertain'] is True
    current = json.loads((c.NATIVE_STATE / 'custody.json').read_bytes())
    assert current['schemaVersion'] == 2 and current['phase'] == 'inactive'
    assert current['launch']['generation'] == result['generation']
    assert set(current['launch']['definitions']) == {c.NATIVE_LABEL + 'sync', c.NATIVE_LABEL + 'direct'}
    assert record['phase'] == 'committed' and record['targetSha256'] == helper.sha(target(n).encode())
    assert helper.environment_quiesce_snapshot(n.selected) is None  # Old archives grant no definition control.
    before = files(n)
    assert helper.publish_environment_generation(n.selected, target(n)) == result
    assert files(n) == before
    with pytest.raises(ValueError, match='recovery-missing'):
        helper.publish_active_slot(n.selected, 'sync', rollback=True)
    assert files(n) == before  # No rollback can restore the old active authority.


def test_same_environment_explicit_retirement_binds_current_generation_and_retries_original_child(executor):
    n = executor
    text = c.NATIVE_ENV.read_text()
    old = json.loads((n.metadata / 'source-jobs-prepare.json').read_bytes())['generation']
    first = helper.publish_environment_generation(n.selected, text, expected_generation=old)
    assert first['previousGeneration'] == old and first['generation'] != old
    before = files(n)
    assert helper.publish_environment_generation(n.selected, text, expected_generation=old) == first
    assert files(n) == before
    job = stopped(n)
    assert job['invocation']['generation'] == first['generation']
    second = helper.publish_environment_generation(n.selected, text, expected_generation=first['generation'])
    assert second['previousGeneration'] == first['generation'] and second['generation'] != first['generation']
    archive = n.metadata / ('.source-environment-' + second['generation'])
    execution = json.loads((archive / 'source-execution.json').read_bytes())
    assert execution['jobs'][c.NATIVE_LABEL + 'sync']['uncertain'] is True
    before = files(n)
    assert helper.publish_environment_generation(n.selected, text, expected_generation=first['generation']) == second
    assert files(n) == before


def test_unrelated_expected_generation_never_withdraws_current_authority(executor):
    n = executor
    before = files(n)
    with pytest.raises(ValueError, match='expected-generation-changed'):
        helper.publish_environment_generation(n.selected, c.NATIVE_ENV.read_text(), expected_generation='f' * 32)
    assert files(n) == before


def abandoned(n, monkeypatch, boundary):
    saved, admit = c._native_save_at, helper.admit_active_slot
    hit = []
    def save(selection, directory, name, value, **kwargs):
        result = saved(selection, directory, name, value, **kwargs)
        if boundary == 'before-slot' and not hit and name == 'source-execution.json' and value['admission'] is not None:
            hit.append(True)
            raise OSError('invented lost admission reply')
        return result
    def admitted(*args, **kwargs):
        result = admit(*args, **kwargs)
        if boundary == 'after-slot' and not hit:
            hit.append(True)
            raise OSError('invented lost admission reply')
        return result
    monkeypatch.setattr(c, '_native_save_at', save)
    monkeypatch.setattr(helper, 'admit_active_slot', admitted)
    with pytest.raises(OSError, match='lost admission reply'):
        n.executor.start('action', 'calendar-1893456789012-12abcdef')
    monkeypatch.setattr(c, '_native_save_at', saved)
    monkeypatch.setattr(helper, 'admit_active_slot', admit)
    assert hit and not n.driver.children


@pytest.mark.parametrize('boundary', ['before-slot', 'after-slot'])
def test_abandoned_one_shot_is_retained_as_unknown_without_dispatch_or_renewal(executor, monkeypatch, boundary):
    n = executor
    abandoned(n, monkeypatch, boundary)
    path = n.metadata / 'source-execution.json'
    old = (path.read_bytes(), path.stat().st_ino)
    pending = json.loads(old[0])['admission']
    old_generation = json.loads((n.metadata / 'source-jobs-prepare.json').read_bytes())['generation']
    result = helper.publish_environment_generation(n.selected, c.NATIVE_ENV.read_text(), expected_generation=old_generation)
    archived = n.metadata / ('.source-environment-' + result['generation']) / 'source-execution.json'
    assert (archived.read_bytes(), archived.stat().st_ino) == old
    assert json.loads(archived.read_bytes())['admission'] == pending
    assert not (n.metadata / 'source-execution.json').exists()
    assert sorted(item['role'] for item in json.loads((n.metadata / 'source-jobs-prepare.json').read_bytes())['definitions'].values()) == ['direct', 'sync']
    assert all(argv[1] not in ('bootstrap', 'kickstart') for argv in n.driver.sent if argv[0] == '/bin/launchctl')
    assert not n.driver.children and result['generation'] != old_generation


@pytest.mark.parametrize('change', ['before-nonce', 'old-boot', 'partial-active'])
def test_forged_or_unsettled_abandoned_admission_preserves_current_authority(executor, monkeypatch, change):
    n = executor
    abandoned(n, monkeypatch, 'after-slot')
    path = n.metadata / 'source-execution.json'
    record = json.loads(path.read_bytes())
    if change == 'before-nonce':
        record['admission']['beforeNonce'] = 'f' * 32
        path.write_bytes(c._native_encoded(record))
    elif change == 'old-boot':
        previous = n.f.bridge.command
        def command(argv, timeout=20):
            if argv == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']:
                return '11234567-89AB-4DEF-8123-0123456789AB\n'
            return previous(argv, timeout=timeout)
        monkeypatch.setattr(n.f.bridge, 'command', command)
    else:
        active = n.metadata / 'source-jobs-active.json'
        value = json.loads(active.read_bytes())
        value['phase'] = 'staging'
        active.write_bytes(c._native_encoded(value))
    before = files(n)
    with pytest.raises((ValueError, c.CustodyError)):
        helper.publish_environment_generation(n.selected, c.NATIVE_ENV.read_text())
    assert files(n) == before and not (n.metadata / 'source-environment-generation.json').exists()


@pytest.mark.parametrize('where', ['gate', 'definition', 'old-jobs', 'v1', 'environment', 'child'])
def test_lost_ack_resumes_original_generation_and_refuses_changed_target(executor, monkeypatch, where):
    n = executor
    original_rename, original_env, original_child = os.rename, c.native_publish_environment, helper.publish_disabled_generation
    hit = []
    def fail_after(action):
        result = action()
        if not hit:
            hit.append(True)
            raise OSError('invented lost acknowledgement')
        return result
    def rename(first, second, **kwargs):
        chosen = (where == 'gate' and second == 'custody.before.json'
            or where == 'definition' and str(first).endswith('.plist')
            or where == 'old-jobs' and first == second == 'native-source-jobs'
            or where == 'v1' and first == '.v1-next')
        return fail_after(lambda: original_rename(first, second, **kwargs)) if chosen and not hit else original_rename(first, second, **kwargs)
    def environment(*args, **kwargs):
        return fail_after(lambda: original_env(*args, **kwargs)) if where == 'environment' and not hit else original_env(*args, **kwargs)
    def child(*args, **kwargs):
        return fail_after(lambda: original_child(*args, **kwargs)) if where == 'child' and not hit else original_child(*args, **kwargs)
    monkeypatch.setattr(os, 'rename', rename)
    monkeypatch.setattr(c, 'native_publish_environment', environment)
    monkeypatch.setattr(helper, 'publish_disabled_generation', child)
    wanted = target(n)
    with pytest.raises(OSError, match='lost acknowledgement'):
        helper.publish_environment_generation(n.selected, wanted)
    frozen = journal(n)
    before = files(n)
    with monkeypatch.context() as read_only:
        read_only.setattr(helper, '_environment_no_actors',
            lambda *_a, **_k: pytest.fail('a quiesce inventory is not an actor-absence proof'))
        inventory = helper.environment_quiesce_snapshot(n.selected)
    assert inventory['phase'] == ('child' if where == 'child' else 'old')
    assert set(inventory['definitions']) == {path.stem for path in n.units.glob('*.plist')}
    assert files(n) == before
    with pytest.raises(ValueError, match='target-changed'):
        helper.publish_environment_generation(n.selected, target(n, 'different-target'))
    assert files(n) == before
    monkeypatch.setattr(os, 'rename', original_rename)
    monkeypatch.setattr(c, 'native_publish_environment', original_env)
    monkeypatch.setattr(helper, 'publish_disabled_generation', original_child)
    result = helper.publish_environment_generation(n.selected, wanted)
    assert hit and result['generation'] == frozen['generation']
    assert result['previousGeneration'] == frozen['previousGeneration']
    assert helper.publish_environment_generation(n.selected, wanted) == result


@pytest.mark.parametrize('change', ['unknown-archive', 'replaced-definition', 'changed-env'])
def test_unknown_images_and_drift_are_preserved_and_refused(executor, monkeypatch, change):
    n = executor
    original = os.rename
    hit = []
    def rename(first, second, **kwargs):
        original(first, second, **kwargs)
        if second == 'custody.before.json' and not hit:
            hit.append(True)
            raise OSError('invented stop')
    monkeypatch.setattr(os, 'rename', rename)
    wanted = target(n)
    with pytest.raises(OSError, match='invented stop'):
        helper.publish_environment_generation(n.selected, wanted)
    monkeypatch.setattr(os, 'rename', original)
    record = journal(n)
    if change == 'unknown-archive':
        path = n.metadata / ('.source-environment-' + record['generation']) / 'unrecorded'
        path.write_bytes(b'preserve unknown')
    elif change == 'replaced-definition':
        path = next(n.units.glob('*.plist'))
        other = path.with_name('replacement')
        other.write_bytes(path.read_bytes())
        other.chmod(0o644)
        other.replace(path)
    else:
        with c.NATIVE_ENV.open('ab') as stream:
            stream.write(b'# foreign drift\n')
    before = files(n)
    with pytest.raises((ValueError, c.CustodyError)):
        helper.publish_environment_generation(n.selected, wanted)
    assert files(n) == before


def test_same_bundle_reboot_uses_fresh_absence_not_old_pid_or_invocation_proof(executor, monkeypatch):
    n = executor
    job = stopped(n)
    old = (n.metadata / 'source-execution.json').read_bytes()
    boot = 'ABCDEF01-2345-4678-8123-ABCDEF012345'
    driver = n.f.bridge.command
    def command(argv, **kwargs):
        if argv == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']:
            return boot + '\n'
        return driver(argv, **kwargs)
    monkeypatch.setattr(n.f.bridge, 'command', command)
    monkeypatch.setattr(c, '_native_boot_uuid', lambda: boot)
    monkeypatch.setattr(ex, 'verify_stopped', lambda *_a, **_k: pytest.fail('old-boot PID proof must not run'))
    current_text = c.NATIVE_ENV.read_text()
    result = helper.publish_environment_generation(n.selected, current_text)
    assert result['generation'] != job['invocation']['generation']
    archive = n.metadata / ('.source-environment-' + result['generation'])
    assert (archive / 'source-execution.json').read_bytes() == old
    assert journal(n)['boot'] == boot
    assert helper.publish_environment_generation(n.selected, current_text) == result


@pytest.mark.parametrize('kind', ['loaded', 'live', 'empty', 'duplicate'])
def test_no_actor_unknown_or_present_never_creates_intent(executor, monkeypatch, kind):
    n = executor
    driver = n.f.bridge.command
    def command(argv, **kwargs):
        if kind == 'loaded' and argv == ['/bin/launchctl', 'print', 'system']:
            return 'system = {\n ' + c.NATIVE_LABEL + 'sync = {}\n}\n'
        if argv == ['/bin/ps', '-axo', 'pid=,uid=']:
            if kind == 'live': return '1 0\n123 ' + str(n.identity['id']) + '\n'
            if kind == 'empty': return ''
            if kind == 'duplicate': return '1 0\n1 0\n'
        return driver(argv, **kwargs)
    monkeypatch.setattr(n.f.bridge, 'command', command)
    before = files(n)
    with pytest.raises((ValueError, c.CustodyError)):
        helper.publish_environment_generation(n.selected, target(n))
    assert files(n) == before and not (n.metadata / 'source-environment-generation.json').exists()


def test_generation_hint_cannot_adopt_another_prepared_generation(executor):
    n = executor
    before = files(n)
    with pytest.raises(ValueError, match='hint-changed'):
        helper.publish_disabled_generation(n.selected, SPECS[:2], _generation='a' * 32)
    assert files(n) == before


def test_environment_template_rejects_loader_injection_before_intent(executor):
    n = executor
    before = files(n)
    with pytest.raises(c.CustodyError, match='(?:environment-invalid|source-broker-env-invalid)'):
        helper.publish_environment_generation(n.selected, target(n) + 'NODE_OPTIONS=--require=/tmp/foreign\n')
    assert files(n) == before and not (n.metadata / 'source-environment-generation.json').exists()


@pytest.fixture
def pending_child(executor, monkeypatch):
    n = executor
    n.wanted = target(n)
    original = helper.publish_disabled_generation
    def lost_reply(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result['status'] == 'inactive'
        raise OSError('invented child publication reply loss')
    with monkeypatch.context() as driver:
        driver.setattr(helper, 'publish_disabled_generation', lost_reply)
        with pytest.raises(OSError, match='child publication reply loss'):
            helper.publish_environment_generation(n.selected, n.wanted)
    n.parent_before = journal(n)
    assert n.parent_before['phase'] == 'generation'
    n.child_before_raw = (n.metadata / 'source-jobs-prepare.json').read_bytes()
    n.child_before_inode = (n.metadata / 'source-jobs-prepare.json').stat().st_ino
    n.child_before = json.loads(n.child_before_raw)
    n.gate_before_boot = ((c.NATIVE_STATE / 'custody.json').read_bytes(), (c.NATIVE_STATE / 'custody.json').stat().st_ino)
    n.defs_before_boot = {path.name: (path.read_bytes(), path.stat().st_ino) for path in n.units.iterdir()}
    n.manifest_before_boot = (n.metadata / 'native-source-jobs/manifest.json').read_bytes()
    n.boot = {'value': 'ABCDEF01-2345-4678-8123-ABCDEF012345'}
    command = n.f.bridge.command
    def observed(argv, **kwargs):
        if argv == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']:
            return n.boot['value'] + '\n'
        return command(argv, **kwargs)
    monkeypatch.setattr(n.f.bridge, 'command', observed)
    monkeypatch.setattr(c, '_native_boot_uuid', lambda: n.boot['value'])
    return n


@pytest.mark.parametrize('where', ['candidate-inode', 'old-journal', 'new-journal', 'parent-reply'])
def test_pending_inactive_child_reboot_rebinds_only_witness_and_resumes_exact_pair(pending_child, monkeypatch, where):
    n = pending_child
    original_save, original_rename = c._native_save_at, os.rename
    hit = []
    def saved(selection, directory, name, value, **kwargs):
        result = original_save(selection, directory, name, value, **kwargs)
        item = (value.get('childBoots') or [None])[-1]
        chosen = (name == 'source-environment-generation.json' and item is not None and (
            where == 'candidate-inode' and item['after']['inode'] is not None and not item['after']['ready']
            or where == 'parent-reply' and item['phase'] == 'committed' and value['phase'] == 'generation'))
        if chosen and not hit:
            hit.append(True)
            raise OSError('invented witness acknowledgement loss')
        return result
    def rename(first, second, **kwargs):
        result = original_rename(first, second, **kwargs)
        chosen = (where == 'old-journal' and str(second).startswith('child-before-')
            or where == 'new-journal' and str(first).startswith('child-next-'))
        if chosen and not hit:
            hit.append(True)
            raise OSError('invented witness acknowledgement loss')
        return result
    monkeypatch.setattr(c, '_native_save_at', saved)
    monkeypatch.setattr(os, 'rename', rename)
    monkeypatch.setattr(ex, 'verify_stopped', lambda *_a, **_k: pytest.fail('no old-boot PID/nonce proof'))
    with pytest.raises(OSError, match='witness acknowledgement loss'):
        helper.publish_environment_generation(n.selected, n.wanted)
    frozen = journal(n)
    assert frozen['generation'] == n.parent_before['generation']
    assert len(frozen['childBoots']) == 1
    current = n.metadata / 'source-jobs-prepare.json'
    inventory = helper.environment_quiesce_snapshot(n.selected)
    assert inventory['phase'] == 'child' and inventory['generation'] == frozen['generation']
    assert inventory['boot'] == (json.loads(current.read_bytes())['witness']['boot']
        if current.exists() else n.child_before['witness']['boot'])
    monkeypatch.setattr(c, '_native_save_at', original_save)
    monkeypatch.setattr(os, 'rename', original_rename)
    result = helper.publish_environment_generation(n.selected, n.wanted)
    child = json.loads(current.read_bytes())
    expected = dict(n.child_before, witness=dict(n.child_before['witness'], boot=n.boot['value']))
    assert child == expected and result['generation'] == frozen['generation']
    assert result['previousGeneration'] == n.parent_before['previousGeneration']
    assert (c.NATIVE_STATE / 'custody.json').read_bytes() == n.gate_before_boot[0]
    assert (c.NATIVE_STATE / 'custody.json').stat().st_ino == n.gate_before_boot[1]
    assert {path.name: (path.read_bytes(), path.stat().st_ino) for path in n.units.iterdir()} == n.defs_before_boot
    assert (n.metadata / 'native-source-jobs/manifest.json').read_bytes() == n.manifest_before_boot
    archive = n.metadata / ('.source-environment-' + result['generation'])
    old_name, _next = helper._environment_boot_names(frozen['childBoots'][0])
    assert (archive / old_name).read_bytes() == n.child_before_raw
    assert (archive / old_name).stat().st_ino == n.child_before_inode
    assert journal(n)['childBoots'][0]['after']['inode'] == frozen['childBoots'][0]['after']['inode']
    before = files(n)
    assert helper.publish_environment_generation(n.selected, n.wanted) == result
    assert files(n) == before and helper.environment_quiesce_snapshot(n.selected) is None


@pytest.mark.parametrize('change', ['activated', 'incomplete', 'replaced-plist', 'execution-intent'])
def test_pending_child_boot_refuses_any_admission_or_unknown_prefix(pending_child, monkeypatch, change):
    n = pending_child
    if change == 'activated':
        n.boot['value'] = n.child_before['witness']['boot']
        helper.publish_active_slot(n.selected, 'sync')
        n.boot['value'] = 'ABCDEF01-2345-4678-8123-ABCDEF012345'
    elif change == 'incomplete':
        path = n.metadata / 'source-jobs-prepare.json'
        value = json.loads(path.read_bytes())
        value['phase'] = 'staging'
        path.write_bytes(c._native_encoded(value))
    elif change == 'replaced-plist':
        path = next(n.units.glob('*.plist'))
        replacement = path.with_name('replacement')
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o644)
        replacement.replace(path)
    else:
        path = n.metadata / 'source-execution.json'
        path.write_bytes(b'{}\n')
        path.chmod(0o600)
    before = files(n)
    with pytest.raises((ValueError, c.CustodyError)):
        helper.publish_environment_generation(n.selected, n.wanted)
    assert files(n) == before and 'childBoots' not in journal(n)
