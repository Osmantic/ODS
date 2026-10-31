"""Native intent excludes fresh writers without stranding admitted recovery.

Temporary owner/Root files and real transaction engines; services and Root UID
custody are simulated. No installed account, model or service is contacted.
"""
import sys
if sys.platform != 'linux':
    from unittest import SkipTest
    raise SkipTest('Combined source/admission transaction fixtures require Linux/WSL')

import contextlib
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'bin'), str(ROOT / 'extensions/services/pixel-agent/host'),
               str(ROOT / 'extensions/services/pixel-agent/tests'),
               str(ROOT / 'tests/pixel_settings'), str(ROOT / 'tests/pixel_inference')]

import pixel_access_bridge as access
import pixel_model_coordinator as model
import pixel_source_upgrade as source
from pixel_connections import native_enrollment as enrollment, native_mcp as native
from pixel_provider import coordinator as provider
from pixel_settings import coordinator as settings
import test_model_coordinator as model_fixture
import test_coordinator as settings_fixture
import test_provider_coordinator as provider_fixture
from test_access_bridge import FakeBridge as AccessBridge
from test_pixel_model_transition import FakeBridge as LegacyBridge
import test_native_enrollment as native_fixture

model_adapter = model_fixture.adapter
settings_adapter = settings_fixture.adapter
integrated, lifecycle, participant = provider_fixture.integrated, provider_fixture.lifecycle, provider_fixture.participant
native_owner, world = native_fixture.owner, native_fixture.world


def native_intent(bridge, case, monkeypatch):
    bridge.state.mkdir(mode=0o700, exist_ok=True)
    path = bridge.state / enrollment.JOURNAL
    record = {'kind': 'native-app-enrollment', 'schemaVersion': 1, 'app': 'notion',
              'flowId': 'b' * 32, 'phase': 'failed' if case == 'terminal' else 'consent',
              'intent': 'connect', 'createdAt': '2026-10-28T12:00:00Z',
              'updatedAt': '2026-10-28T12:00:00Z', 'activationId': None,
              'step': None, 'stable': None, 'proof': None, 'error': None}
    access.atomic_json(path, enrollment.validate_journal(record))
    if case == 'invalid':
        path.write_text('{broken')
    if case == 'unavailable':
        def unavailable(_path):
            raise PermissionError('fixture detector unavailable')
        monkeypatch.setattr(enrollment, '_read', unavailable)


def files(root):
    return {str(path.relative_to(root)): (path.stat().st_mode & 0o777, path.read_bytes())
            for path in root.rglob('*') if path.is_file()}


def monitor(bridge, monkeypatch):
    previous_lock, previous_guard = bridge.locked, enrollment.guard_idle
    observed = {'locked': False, 'guards': 0}

    @contextlib.contextmanager
    def locked(*args, **kwargs):
        assert not observed['locked'], 'Writer acquired a nested Root lock'
        with previous_lock(*args, **kwargs):
            observed['locked'] = True
            try:
                yield
            finally:
                observed['locked'] = False

    def guard(current):
        assert observed['locked'], 'Native intent was read outside the Root lock'
        assert current.state == bridge.state
        observed['guards'] += 1
        return previous_guard(current)

    monkeypatch.setattr(bridge, 'locked', locked)
    monkeypatch.setattr(enrollment, 'guard_idle', guard)
    return observed


def refuses(bridge, invoke, case, root, monkeypatch, *, observe=None):
    with bridge.locked():
        pass
    native_intent(bridge, case, monkeypatch)
    if observe:
        observe()  # Read-only status remains available even if intent is corrupt.
    observed = monitor(bridge, monkeypatch)
    before = files(root)
    runtime = {key: copy.deepcopy(getattr(bridge, key)) for key in
               ('pid', 'native_phase', 'edge_phase', 'native_state', 'edge_state', 'stops', 'restarts')
               if hasattr(bridge, key)}
    error = {'pending': 'native-app-transition-in-progress', 'invalid': 'invalid-native-mcp-state',
             'unavailable': 'native-app-transition-unavailable'}[case]
    with pytest.raises(access.AccessError, match='^' + error + '$'):
        invoke()
    assert observed == {'locked': False, 'guards': 1}
    assert files(root) == before
    assert {key: getattr(bridge, key) for key in runtime} == runtime


def source_intent(bridge, case, monkeypatch):
    bridge.state.mkdir(mode=0o700, exist_ok=True)
    if not hasattr(bridge, 'install'):
        bridge.install = bridge.state / 'fixture-install'
        bridge.install.mkdir(mode=0o700)
    if not hasattr(bridge, 'owner'):
        bridge.owner = SimpleNamespace(pw_uid=os.getuid())
    child = bridge.state / 'source-upgrade'
    child.mkdir(mode=0o700)
    manager = source.SourceUpgrade(child, bridge.install, bridge.owner.pw_uid, state_uid=os.geteuid())
    value = {'version': 1, 'install': str(bridge.install), 'uid': bridge.owner.pw_uid,
             'identity': {'beforeRef': 'a' * 40, 'afterRef': 'b' * 40, 'markerSha256': 'c' * 64,
                          'configSha256': 'd' * 64, 'receiptSha256': None},
             'before': {}, 'after': {}, 'candidate': {}, 'hold': 'e' * 64, 'phase': 'held', 'outcome': None}
    manager._save(value)
    manager.journal()
    if case == 'invalid':
        (child / 'source-upgrade.json').write_text('{broken')
    elif case == 'unavailable':
        def unavailable(self):
            raise PermissionError('fixture source journal unavailable')
        monkeypatch.setattr(source.SourceUpgrade, 'journal', unavailable)


def refuses_source(bridge, invoke, case, root, monkeypatch, *, observe):
    source_intent(bridge, case, monkeypatch)
    assert bridge.pending() is None
    observe()
    observed = monitor(bridge, monkeypatch)
    before = files(root)
    runtime = {key: copy.deepcopy(getattr(bridge, key)) for key in
               ('pid', 'native_phase', 'edge_phase', 'native_state', 'edge_state', 'stops', 'restarts')
               if hasattr(bridge, key)}
    error = 'source-transition-in-progress' if case == 'held' else 'source-transition-unavailable'
    with pytest.raises(access.AccessError, match='^' + error + '$'):
        invoke()
    assert observed == {'locked': False, 'guards': 1}
    assert files(root) == before
    assert {key: getattr(bridge, key) for key in runtime} == runtime


@pytest.mark.parametrize('case', ['pending', 'invalid', 'unavailable'])
def test_model_begin_checks_native_intent_before_snapshots_or_leases(model_adapter, case, tmp_path, monkeypatch):
    bridge = model_adapter
    request = {'revision': model.control(bridge, 'model-status')['revision'], 'transactionId': 'a' * 64}
    refuses(bridge, lambda: model.control(bridge, 'model-begin', request), case, tmp_path, monkeypatch,
            observe=lambda: model.control(bridge, 'model-status'))
    assert not (bridge.state / 'model-before.json').exists()


@pytest.mark.parametrize('case', ['pending', 'invalid', 'unavailable'])
def test_settings_apply_checks_native_intent_before_store_or_preparation(settings_adapter, case, tmp_path, monkeypatch):
    bridge = settings_adapter
    request = settings_fixture.request(bridge)
    refuses(bridge, lambda: settings.change(bridge, request), case, tmp_path, monkeypatch,
            observe=lambda: settings.status(bridge))


@pytest.mark.parametrize('operation', ['apply', 'deactivate'])
@pytest.mark.parametrize('case', ['pending', 'invalid', 'unavailable'])
def test_provider_changes_check_native_intent_before_store_or_service_writes(integrated, operation, case, tmp_path, monkeypatch):
    bridge = integrated.bridge
    request = provider_fixture.request(integrated, operation)
    refuses(bridge, lambda: provider.change(bridge, request), case, tmp_path, monkeypatch,
            observe=lambda: provider.status(bridge))


@pytest.mark.parametrize('case', ['pending', 'invalid', 'unavailable'])
@pytest.mark.parametrize('kind', ['model', 'access'])
def test_legacy_model_and_fresh_access_change_check_native_intent(kind, case, tmp_path, monkeypatch):
    if kind == 'model':
        bridge = LegacyBridge(tmp_path)
        invoke = bridge.model_begin
        observe = bridge.model_status
    else:
        original = access.private_json
        monkeypatch.setattr(access, 'private_json', lambda path, uid, maximum=1024 * 1024:
                            original(path, os.getuid(), maximum))
        bridge = AccessBridge(tmp_path)
        request = {'mode': 'full-access', 'confirmed': True, 'revision': bridge.status()['revision']}
        invoke = lambda: bridge.change(request)
        observe = bridge.status
    refuses(bridge, invoke, case, tmp_path, monkeypatch, observe=observe)


@pytest.mark.parametrize('case', ['held', 'invalid', 'unavailable'])
def test_source_reservation_excludes_new_model_route(model_adapter, case, tmp_path, monkeypatch):
    bridge = model_adapter
    request = {'revision': model.control(bridge, 'model-status')['revision'], 'transactionId': 'a' * 64}
    refuses_source(bridge, lambda: model.control(bridge, 'model-begin', request), case, tmp_path, monkeypatch,
                   observe=lambda: model.control(bridge, 'model-status'))
    assert not (bridge.state / 'model-before.json').exists()


@pytest.mark.parametrize('case', ['held', 'invalid', 'unavailable'])
def test_source_reservation_excludes_settings_before_store_preparation(settings_adapter, case, tmp_path, monkeypatch):
    bridge = settings_adapter
    request = settings_fixture.request(bridge)
    refuses_source(bridge, lambda: settings.change(bridge, request), case, tmp_path, monkeypatch,
                   observe=lambda: settings.status(bridge))


@pytest.mark.parametrize('operation', ['apply', 'deactivate'])
@pytest.mark.parametrize('case', ['held', 'invalid', 'unavailable'])
def test_source_reservation_excludes_provider_before_environment_or_service_preparation(
        integrated, operation, case, tmp_path, monkeypatch):
    bridge = integrated.bridge
    request = provider_fixture.request(integrated, operation)
    refuses_source(bridge, lambda: provider.change(bridge, request), case, tmp_path, monkeypatch,
                   observe=lambda: provider.status(bridge))


@pytest.mark.parametrize('case', ['held', 'invalid', 'unavailable'])
@pytest.mark.parametrize('kind', ['model', 'access'])
def test_source_reservation_excludes_fresh_legacy_model_or_access(kind, case, tmp_path, monkeypatch):
    if kind == 'model':
        bridge = LegacyBridge(tmp_path)
        invoke, observe = bridge.model_begin, bridge.model_status
    else:
        original = access.private_json
        monkeypatch.setattr(access, 'private_json', lambda path, uid, maximum=1024 * 1024:
                            original(path, os.getuid(), maximum))
        bridge = AccessBridge(tmp_path)
        request = {'mode': 'full-access', 'confirmed': True, 'revision': bridge.status()['revision']}
        invoke, observe = lambda: bridge.change(request), bridge.status
    refuses_source(bridge, invoke, case, tmp_path, monkeypatch, observe=observe)


@pytest.mark.parametrize('case', ['pending', 'invalid', 'unavailable'])
def test_existing_model_apply_and_rollback_finish_remain_admitted(model_adapter, case, monkeypatch):
    bridge = model_adapter
    model_fixture.begin(bridge)
    native_intent(bridge, case, monkeypatch)
    observed = monitor(bridge, monkeypatch)
    model_fixture.apply(bridge)
    assert model_fixture.finish(bridge, 'rollback')['outcome'] == 'rollback'
    assert not bridge.pending() and observed['guards'] == 0


@pytest.mark.parametrize('case', ['pending', 'invalid', 'unavailable'])
@pytest.mark.parametrize('failure', ['preinvoke', 'lost-reply'])
def test_existing_settings_recovery_does_not_depend_on_native_detector(settings_adapter, case, failure, monkeypatch):
    bridge = settings_adapter
    bridge.failure = failure
    with pytest.raises(access.AccessError):
        settings.change(bridge, settings_fixture.request(bridge))
    bridge.failure = None
    native_intent(bridge, case, monkeypatch)
    observed = monitor(bridge, monkeypatch)
    result = settings.change(bridge, settings_fixture.request(bridge, 'recover'))
    assert result['outcome'] == ('applied' if failure == 'lost-reply' else 'rolled-back')
    assert not bridge.pending() and observed['guards'] == 0


@pytest.mark.parametrize('case', ['pending', 'invalid', 'unavailable'])
@pytest.mark.parametrize('failure', ['reload', 'lost-reply'])
def test_existing_provider_recovery_and_verified_completion_remain_admitted(integrated, case, failure, monkeypatch):
    bridge = integrated.bridge
    bridge.failure = failure
    with pytest.raises(access.AccessError):
        provider.change(bridge, provider_fixture.request(integrated))
    bridge.failure = None
    native_intent(bridge, case, monkeypatch)
    observed = monitor(bridge, monkeypatch)
    result = provider.change(bridge, provider_fixture.request(integrated, 'recover'))
    assert result['outcome'] == ('applied' if failure == 'lost-reply' else 'rolled-back')
    assert not bridge.pending() and observed['guards'] == 0


@pytest.mark.parametrize('case', ['pending', 'invalid', 'unavailable'])
def test_existing_sandbox_restore_does_not_depend_on_native_detector(tmp_path, monkeypatch, case):
    original = access.private_json
    monkeypatch.setattr(access, 'private_json', lambda path, uid, maximum=1024 * 1024:
                        original(path, os.getuid(), maximum))
    bridge = AccessBridge(tmp_path)
    request = {'mode': 'full-access', 'confirmed': True, 'revision': bridge.status()['revision']}
    bridge.fail = 'probe'
    with pytest.raises(access.AccessError):
        bridge.change(request)
    assert bridge.pending()
    bridge.fail = None
    native_intent(bridge, case, monkeypatch)
    observed = monitor(bridge, monkeypatch)
    result = bridge.change({'mode': 'sandboxed', 'confirmed': False, 'revision': bridge.status()['revision']})
    assert result['runtime_verified'] and result['effective_mode'] == 'sandboxed'
    assert not bridge.pending() and observed['guards'] == 0


@pytest.fixture
def source_stage(tmp_path, monkeypatch):
    state = tmp_path / 'root-state'
    state.mkdir(mode=0o700)
    (state / 'lock').touch(mode=0o600)
    previous_directory, previous_fstat = source.directory, os.fstat
    monkeypatch.setattr(source, 'directory', lambda path, uid, **kwargs:
                        previous_directory(path, os.getuid() if uid == 0 else uid, **kwargs))

    def fstat(fd):
        info = previous_fstat(fd)
        lock = (state / 'lock').stat()
        if (info.st_dev, info.st_ino) == (lock.st_dev, lock.st_ino):
            fields = list(info)
            fields[4] = 0
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(source.os, 'fstat', fstat)
    observed = {'locked': False, 'creates': 0, 'stages': 0}
    previous_lock, previous_guard = source.admission_lock, enrollment.guard_idle

    @contextlib.contextmanager
    def locked(current):
        assert current == state and not observed['locked']
        with previous_lock(current):
            observed['locked'] = True
            try:
                yield
            finally:
                observed['locked'] = False

    def guard(current):
        assert observed['locked'] and current.state == state
        # Real flock contention confirms the source writer already owns Root's
        # admission lock; a second acquisition must refuse, not nest or wait.
        with pytest.raises(source.UpgradeError, match='^source-admission-busy$'):
            with previous_lock(state):
                raise AssertionError('Nested admission was allowed')
        return previous_guard(current)

    def manager(install, owner, *, create=False):
        if create:
            assert observed['locked']
            observed['creates'] += 1
            (state / 'source-upgrade').mkdir(mode=0o700)
            return SimpleNamespace(state=state / 'source-upgrade'), None, state
        return None, None, state

    def stage(*_args):
        assert observed['locked']
        observed['stages'] += 1

    monkeypatch.setattr(source, 'admission_lock', locked)
    monkeypatch.setattr(enrollment, 'guard_idle', guard)
    monkeypatch.setattr(source, '_manager', manager)
    monkeypatch.setattr(source, '_stage', stage)
    return state, observed


@pytest.mark.parametrize('case', ['pending', 'invalid', 'unavailable'])
def test_source_stage_refuses_before_even_creating_its_first_directory(source_stage, case, tmp_path, monkeypatch, capsys):
    state, observed = source_stage
    native_intent(SimpleNamespace(state=state), case, monkeypatch)
    before = files(tmp_path)
    source.main(['status', '/fixture/install', 'fixture'])
    assert json.loads(capsys.readouterr().out) == {'pending': False}
    with pytest.raises(source.UpgradeError):
        source.main(['stage', '/fixture/install', 'fixture', '/fixture/candidate', 'a' * 40])
    assert observed == {'locked': False, 'creates': 0, 'stages': 0}
    assert files(tmp_path) == before


def test_terminal_native_intent_permits_source_stage_under_the_same_lock(source_stage, monkeypatch):
    state, observed = source_stage
    native_intent(SimpleNamespace(state=state), 'terminal', monkeypatch)
    source.main(['stage', '/fixture/install', 'fixture', '/fixture/candidate', 'a' * 40])
    assert observed == {'locked': False, 'creates': 1, 'stages': 1}


@pytest.fixture
def retained_source():
    # _protected_json correctly rejects /tmp's writable ancestor. Keep all
    # fixture state in a new private directory, never in the real owner config.
    with tempfile.TemporaryDirectory(prefix='.ods-native-admission-', dir=Path.home()) as temporary:
        root = Path(temporary)
        state, home, candidate = (root / name for name in ('state', 'owner', 'candidate'))
        for directory in (state, home, candidate):
            directory.mkdir(mode=0o700)
        install, child = home / 'ods', state / 'source-upgrade'
        install.mkdir(mode=0o700)
        child.mkdir(mode=0o700)
        for directory in (install, candidate):
            for name in source.ROOTS:
                (directory / name).mkdir(mode=0o755)
            path = directory / 'bin/example.py'
            path.write_text('old' if directory == install else 'new')
            path.chmod(0o644)
        config = home / '.openclaw'
        config.mkdir(mode=0o700)
        access.atomic_json(config / 'openclaw.json', {'fixture': 'before'})
        marker = home / '.config/ods'
        marker.mkdir(mode=0o700, parents=True)
        access.atomic_json(marker / 'pixel-managed.json', {'fixture': 'bound-owner-marker'})
        account = SimpleNamespace(pw_dir=str(home), pw_uid=os.getuid())
        identity = dict(beforeRef='a' * 40, afterRef='b' * 40,
                        markerSha256=source._protected_json(marker / 'pixel-managed.json', os.getuid())[1],
                        **source.owner_baseline(home, os.getuid()))
        manager = source.SourceUpgrade(child, install, os.getuid(), state_uid=os.getuid())
        manager.stage(candidate, os.getuid(), identity)
        manager.bind('d' * 64, lambda token: None)
        pending = {'kind': 'model', 'transaction_id': 'd' * 64, 'token': 'e' * 64, 'phase': 'held',
                   'edge_revision': 'f' * 64, 'configured_mode': 'sandboxed',
                   'start_config_sha256': identity['configSha256']}
        yield manager, account, state, pending


@pytest.mark.parametrize('root_journal', [True, False])
def test_exact_retained_source_recovery_is_not_blocked_by_unreadable_native_intent(
        retained_source, root_journal, tmp_path, monkeypatch):
    manager, account, state, pending = retained_source
    if root_journal:
        access.atomic_json(state / 'transition.json', pending)
        original = source._protected_json
        monkeypatch.setattr(source, '_protected_json', lambda path, uid=0:
                            original(path, os.getuid() if uid == 0 else uid))
    native_intent(SimpleNamespace(state=state), 'unavailable', monkeypatch)
    before = files(manager.state.parent.parent)
    source._guard_native_stage(manager, account, state)
    assert files(manager.state.parent.parent) == before


@pytest.mark.parametrize('fault', ['transaction', 'config', 'phase', 'kind', 'unheld'])
def test_source_does_not_adopt_an_unrelated_or_invalid_existing_root_hold(
        retained_source, fault, tmp_path, monkeypatch):
    manager, account, state, pending = retained_source
    if fault == 'unheld':
        value = manager.journal()
        value.update(phase='staged', hold=None)
        manager._save(value)
    else:
        key, value = {'transaction': ('transaction_id', 'a' * 64), 'config': ('start_config_sha256', 'a' * 64),
                      'phase': ('phase', 'unknown'), 'kind': ('kind', 'connection')}[fault]
        pending[key] = value
        access.atomic_json(state / 'transition.json', pending)
    original = source._protected_json
    monkeypatch.setattr(source, '_protected_json', lambda path, uid=0:
                        original(path, os.getuid() if uid == 0 else uid))
    native_intent(SimpleNamespace(state=state), 'pending', monkeypatch)
    before = files(manager.state.parent.parent)
    with pytest.raises(source.UpgradeError):
        source._guard_native_stage(manager, account, state)
    assert files(manager.state.parent.parent) == before


@pytest.mark.parametrize('fault', ['config', 'receipt', 'marker', 'source'])
def test_reserved_source_without_root_journal_requires_the_exact_original_before_image(
        retained_source, fault, tmp_path, monkeypatch):
    manager, account, state, pending = retained_source
    home = Path(account.pw_dir)
    if fault == 'config':
        (home / '.openclaw/openclaw.json').write_text('{"fixture":"changed"}')
    elif fault == 'receipt':
        directory = home / '.local/state/ods-pixel-access-mode'
        directory.mkdir(parents=True, mode=0o700)
        access.atomic_json(directory / 'pixel-access-mode.json', {'fixture': 'changed'})
    elif fault == 'marker':
        (home / '.config/ods/pixel-managed.json').write_text('{"fixture":"changed"}')
    else:
        (manager.install / 'bin/example.py').write_text('changed')
    native_intent(SimpleNamespace(state=state), 'pending', monkeypatch)
    before = files(manager.state.parent.parent)
    with pytest.raises(source.UpgradeError, match='^source-owner-state-changed$'):
        source._guard_native_stage(manager, account, state)
    assert files(manager.state.parent.parent) == before


@pytest.mark.parametrize('phase,hold,outcome,allowed', [
    ('staged', None, None, True), ('complete', 'd' * 64, 'applied', True),
    ('complete', 'd' * 64, 'rolled-back', True), ('held', None, None, False),
    ('staged', 'd' * 64, None, False), ('complete', None, None, False),
])
def test_source_admission_allows_only_unreserved_or_closed_plans(
        retained_source, phase, hold, outcome, allowed):
    manager, account, state, pending = retained_source
    value = manager.journal()
    value.update(phase=phase, hold=hold, outcome=outcome)
    manager._save(value)
    bridge = SimpleNamespace(state=state, install=manager.install, owner=account)
    before = files(manager.state.parent.parent)
    if allowed:
        source.guard_fresh_admission(bridge)
    else:
        with pytest.raises(source.UpgradeError, match='^source-transition-in-progress$'):
            source.guard_fresh_admission(bridge)
    assert files(manager.state.parent.parent) == before


def test_terminal_native_intent_allows_a_new_model_transaction(model_adapter, monkeypatch):
    native_intent(model_adapter, 'terminal', monkeypatch)
    model_fixture.begin(model_adapter)
    assert model_fixture.finish(model_adapter, 'rollback')['outcome'] == 'rollback'


def test_terminal_native_intent_allows_settings_apply(settings_adapter, monkeypatch):
    native_intent(settings_adapter, 'terminal', monkeypatch)
    assert settings.change(settings_adapter, settings_fixture.request(settings_adapter))['outcome'] == 'applied'


def test_terminal_native_intent_allows_provider_apply_and_deactivate(integrated, monkeypatch):
    native_intent(integrated.bridge, 'terminal', monkeypatch)
    assert provider.change(integrated.bridge, provider_fixture.request(integrated))['outcome'] == 'applied'
    assert provider.change(integrated.bridge, provider_fixture.request(integrated, 'deactivate'))['outcome'] == 'applied'


@pytest.mark.parametrize('case', ['pending', 'invalid', 'unavailable'])
def test_legacy_model_finish_keeps_its_original_held_transaction(case, tmp_path, monkeypatch):
    bridge = LegacyBridge(tmp_path)
    result = bridge.model_begin()
    native_intent(bridge, case, monkeypatch)
    observed = monitor(bridge, monkeypatch)
    original = access.private_json
    monkeypatch.setattr(access, 'private_json', lambda path, uid, maximum=1024 * 1024:
                        original(path, os.getuid(), maximum))
    assert bridge.model_status()['transaction_id'] == result['transaction_id']
    assert bridge.model_finish({'transaction_id': result['transaction_id'], 'outcome': 'rolled-back'})['status'] == 'released'
    assert not bridge.pending() and observed['guards'] == 0


def test_isolated_source_stage_loads_only_the_fixed_sibling_guard(tmp_path, monkeypatch):
    state, cwd = tmp_path / 'state', tmp_path / 'untrusted-cwd'
    state.mkdir(mode=0o700)
    cwd.mkdir(mode=0o700)
    (cwd / 'pixel_access_bridge.py').write_text("raise RuntimeError('cwd module must not load')\n")
    native_intent(SimpleNamespace(state=state), 'pending', monkeypatch)
    script = (
        'import importlib.util,pathlib,sys; '
        'spec=importlib.util.spec_from_file_location("isolated_source",sys.argv[1]); '
        'module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module); '
        '\ntry: module._guard_native_stage(None,None,pathlib.Path(sys.argv[2]))'
        '\nexcept module.UpgradeError as error: print(str(error))'
    )
    before = files(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-c', script, str(ROOT / 'bin/pixel_source_upgrade.py'), str(state)],
                            cwd=cwd, capture_output=True, text=True, timeout=15, check=True)
    assert result.stdout.strip() == 'native-app-transition-in-progress' and result.stderr == ''
    assert files(tmp_path) == before


@pytest.mark.parametrize('source_case', ['held', 'invalid', 'unavailable'])
def test_fresh_native_enrollment_refuses_source_reservation_without_a_root_journal(
        native_owner, source_case, tmp_path, monkeypatch):
    bridge = native_owner.bridge
    with bridge.locked():
        bridge.discover()
        source_intent(bridge, source_case, monkeypatch)
    assert bridge.pending() is None
    before = files(tmp_path)
    with pytest.raises(native.NativeError, match='^source-transition-(in-progress|unavailable)$'):
        native_owner.controller.begin(redirect_port=43123)
    assert files(tmp_path) == before
    assert native_owner.native_calls == [] and native_owner.preflights == []


@pytest.mark.parametrize('phase', ['staged', 'complete'])
def test_unreserved_or_closed_source_allows_new_app_but_cannot_restage_across_its_consent(
        native_owner, phase, tmp_path, monkeypatch):
    bridge = native_owner.bridge
    with bridge.locked():
        bridge.discover()
        source_intent(bridge, 'held', monkeypatch)
        manager = source.SourceUpgrade(bridge.state / 'source-upgrade', bridge.install,
                                       bridge.owner.pw_uid, state_uid=os.geteuid())
        value = manager.journal()
        value.update(phase=phase, hold=None if phase == 'staged' else value['hold'],
                     outcome=None if phase == 'staged' else 'applied')
        manager._save(value)
    assert native_owner.controller.begin(redirect_port=43123)['phase'] == 'consent'
    before = files(tmp_path)
    with bridge.locked(), pytest.raises(source.UpgradeError, match='^native-app-transition-in-progress$'):
        source._guard_native_stage(manager, bridge.owner, bridge.state)
    assert files(tmp_path) == before and manager.journal() == value
