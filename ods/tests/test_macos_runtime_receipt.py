import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load('runtime_receipt', ROOT / 'installers/macos/lib/pixel-runtime-receipt.py')
upgrade = load('receipt_upgrade', ROOT / 'installers/macos/lib/pixel-runtime-upgrade.py')
controller = load('receipt_controller_test', ROOT / 'extensions/services/pixel-agent/host/pixel_access_mode.py')


@pytest.fixture
def receipt(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    root.chmod(0o700)
    old = root / 'old.json'
    new = root / 'new.json'
    config = {'agents': {'list': [{'id': 'pixel', 'sandbox': {'mode': 'all'},
        'tools': {'exec': {'host': 'sandbox', 'security': 'allowlist', 'ask': 'always'},
                  'fs': {'workspaceOnly': True}}}]}}
    old.write_text(json.dumps(config))
    old.chmod(0o600)
    state = root / '.ods-access-mode'
    monkeypatch.setattr(controller, '_default_state_dir', lambda: str(root / 'missing-legacy'))
    def selection():
        new.write_bytes(old.read_bytes())
        new.chmod(0o600)
        return dict(source=str(old), target=str(new),
            sourceHash=hashlib.sha256(old.read_bytes()).hexdigest(),
            targetHash=hashlib.sha256(new.read_bytes()).hexdigest())
    return old, new, state, selection


def test_sandbox_without_receipt_does_not_invent_baseline(receipt):
    old, new, state, select = receipt
    assert helper.relocate(select(), controller) is False
    assert not (state / controller.RECEIPT_NAME).exists()


def test_full_access_relocates_and_rolls_back_preserving_baseline(receipt):
    old, new, state, select = receipt
    controller.enable_full_access(str(old), str(state), confirmed=True,
        validate_config=lambda _: True, restart=lambda: True, check_no_active_run=lambda: False)
    original = controller._load_receipt(str(state))
    value = select()
    assert helper.relocate(value, controller) is True
    assert helper.relocate(value, controller) is False
    assert helper.relocate(dict(source=value['target'], target=value['source'],
        sourceHash=value['targetHash'], targetHash=value['sourceHash']), controller) is True
    assert controller._load_receipt(str(state)) == original


def test_missing_full_access_receipt_is_not_silently_skipped(receipt):
    old, new, state, select = receipt
    config, _ = controller._helper.enable(json.loads(old.read_bytes()))
    old.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='full-access-receipt-required'):
        helper.relocate(select(), controller)


def test_hash_drift_and_cross_directory_are_rejected(receipt):
    old, new, state, select = receipt
    value = select()
    with pytest.raises(ValueError, match='configuration-changed'):
        helper.relocate(dict(value, targetHash='0' * 64), controller)
    with pytest.raises(ValueError, match='invalid-relocation-selection'):
        helper.relocate(dict(value, target='/other/new.json'), controller)


@pytest.mark.skipif(sys.platform != 'darwin' or os.geteuid() == 0,
                    reason='requires an unprivileged macOS process')
def test_installer_python_runs_owner_helper_in_isolation_with_spaces(tmp_path):
    root = tmp_path.resolve() / 'Installation with spaces'
    root.mkdir(mode=0o700)
    state = root / '.ods-access-mode'
    old, new = root / 'old.json', root / 'new.json'
    cfg = {'agents': {'list': [{'id': 'pixel', 'sandbox': {'mode': 'all'},
        'tools': {'exec': {'host': 'sandbox', 'security': 'allowlist', 'ask': 'always'},
                  'fs': {'workspaceOnly': True}}}]}}
    old.write_text(json.dumps(cfg))
    old.chmod(0o600)
    controller.enable_full_access(str(old), str(state), confirmed=True,
        validate_config=lambda _: True, restart=lambda: True, check_no_active_run=lambda: False)
    original = controller._load_receipt(str(state))
    new.write_bytes(old.read_bytes())
    new.chmod(0o600)
    digest = hashlib.sha256(old.read_bytes()).hexdigest()
    script = ROOT / 'installers/macos/lib/pixel-runtime-receipt.py'
    def invoke(source, target):
        return subprocess.run(['/usr/bin/python3', '-I', str(script)],
            input=json.dumps(dict(source=str(source), target=str(target),
                                  sourceHash=digest, targetHash=digest)) + '\n',
            env={'HOME': str(root), 'PATH': '/usr/bin:/bin'}, cwd='/',
            text=True, capture_output=True, timeout=30, check=False)
    for source, target, changed in ((old, new, True), (old, new, False), (new, old, True)):
        result = invoke(source, target)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {'relocated': changed}
    assert controller._load_receipt(str(state)) == original
    new.write_text('{}')
    result = invoke(old, new)
    assert result.returncode == 1
    assert not result.stdout
    assert result.stderr.strip() == 'runtime-receipt-relocation-failed'
    assert controller._load_receipt(str(state)) == original


@pytest.mark.skipif(sys.platform != 'darwin' or os.geteuid() == 0,
                    reason='requires an unprivileged macOS process')
@pytest.mark.parametrize('failure', [None, 'after-owner-write', 'candidate-ready', 'rollback-drift'])
def test_real_owner_process_participates_in_upgrade_rollback(receipt, failure):
    old, new, state, select = receipt
    controller.enable_full_access(str(old), str(state), confirmed=True,
        validate_config=lambda _: True, restart=lambda: True, check_no_active_run=lambda: False)
    original = controller._load_receipt(str(state))
    value = select()
    loaded = dict.fromkeys(('gateway', 'access', 'relay'), 'previous')
    files = ['previous']
    phases = []
    def service(role, version):
        def stopped():
            assert role not in loaded
        return SimpleNamespace(target='system/test-' + role, role=role, version=version,
                               assert_stopped=stopped)
    previous = {role: service(role, 'previous') for role in loaded}
    candidate = {role: service(role, 'candidate') for role in loaded}
    def stop(service):
        loaded.pop(service.role, None)
    def start(service):
        assert files == [service.version] and service.role not in loaded
        loaded[service.role] = service.version
    def replace():
        assert not loaded
        files[:] = ['candidate']
    def restore():
        assert not loaded
        assert controller._load_receipt(str(state)) == original
        files[:] = ['previous']
    def owner(reverse=False):
        assert not loaded
        selection = dict(value)
        if reverse:
            selection = dict(source=value['target'], target=value['source'],
                             sourceHash=value['targetHash'], targetHash=value['sourceHash'])
        result = subprocess.run(['/usr/bin/python3', '-I', str(ROOT /
            'installers/macos/lib/pixel-runtime-receipt.py')], input=json.dumps(selection) + '\n',
            env={'HOME': str(old.parent), 'PATH': '/usr/bin:/bin'}, cwd='/',
            text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('owner-failed')
        assert json.loads(result.stdout) == {'relocated': True}
        if not reverse and failure == 'after-owner-write':
            raise RuntimeError('reply-lost')
    def ready(services):
        version = services['gateway'].version
        assert loaded == dict.fromkeys(previous, version)
        if version == 'candidate' and failure in ('candidate-ready', 'rollback-drift'):
            if failure == 'rollback-drift':
                new.write_text('{}')
            raise RuntimeError('candidate-not-ready')
    args = dict(previous=previous, candidate=candidate, phase=phases.append,
        verify=lambda: None, replace_files=replace, restore_files=restore, start=start,
        stop=stop, ready=ready, migrate_owner=owner, restore_owner=lambda: owner(True))
    if failure:
        with pytest.raises(RuntimeError):
            upgrade.activate(**args)
        if failure == 'rollback-drift':
            assert phases[-1] == 'recovery-required' and not loaded and files == ['candidate']
        else:
            assert phases[-1] == 'restored' and files == ['previous']
            assert controller._load_receipt(str(state)) == original
    else:
        upgrade.activate(**args)
        assert phases[-1] == 'active' and files == ['candidate']
        assert controller._load_receipt(str(state)) == dict(original, config_path=str(new),
                                                          config_sha256=value['targetHash'])
