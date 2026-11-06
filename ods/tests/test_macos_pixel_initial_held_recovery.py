"""A held initial recovery must prove its exact private owner transaction."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
from pixel_access_bridge import AccessError  # noqa: E402

spec = importlib.util.spec_from_file_location('initial_held_install',
    ROOT / 'installers/macos/lib/pixel-macos-access-install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def held(tmp_path, monkeypatch):
    install = tmp_path.resolve() / 'ods'
    preparation = install / 'data/pixel-native/preparation'
    preparation.mkdir(parents=True, mode=0o700)
    directory = preparation / ('ingress-refresh-' + 'a' * 32)
    directory.mkdir(mode=0o700)
    pointer = preparation / 'ingress-refresh-current.json'
    journal = directory / 'refresh.json'
    source = install / 'data/pixel-native/home/.openclaw/openclaw.json'
    binding = {'token': 'b' * 64, 'revision': 'c' * 64}
    record = {'schemaVersion': 1, 'phase': 'operation-failed',
        'operation': {'kind': 'retained-recovery', 'key': 'd' * 64},
        'binding': binding, 'project': 'ods', 'source': str(source),
        'selection': {'sha256': hashlib.sha256(b'config').hexdigest()},
        'rollback': {'path': str(directory / 'rollback.compose.json')}}
    def write(path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    write(pointer, {'directory': directory.name})
    write(journal, record)
    plan = {'owner': SimpleNamespace(pw_uid=os.getuid()),
        'access_settings': {'install_dir': str(install)},
        'source_environment': {'PIXEL_HISTORY_PROJECT': 'ods'},
        'runtime_bundle': {'source_config': str(source), 'source_config_bytes': b'config'}}
    observed = {'revision': binding['revision']}
    calls = []
    def request(actual_plan, container, action, actual_binding):
        assert actual_plan is plan and container == 'e' * 64 and action == 'acquire'
        calls.append(dict(actual_binding))
        if actual_binding != binding:
            raise AccessError('edge-transition-token-mismatch')
        return {'capability': 'available', 'phase': 'held', 'streams': 0,
            'admission_blocked': True, 'revision': binding['revision']}
    monkeypatch.setattr(installer, '_migration_edge_request', request)
    return SimpleNamespace(plan=plan, observed=observed, pointer=pointer, journal=journal,
        directory=directory, record=record, binding=binding, calls=calls, write=write)


@pytest.mark.parametrize('phase', ['operating', 'operation-failed'])
def test_exact_retained_hold_is_rechecked_without_changing_receipts(held, phase):
    held.record['phase'] = phase
    held.write(held.journal, held.record)
    before = (held.pointer.read_bytes(), held.journal.read_bytes())
    installer._verify_initial_retained_hold(held.plan, 'e' * 64, held.observed)
    assert held.calls == [held.binding]
    assert (held.pointer.read_bytes(), held.journal.read_bytes()) == before


@pytest.mark.parametrize('fault', ['wrong-kind', 'wrong-phase', 'wrong-project', 'wrong-source',
    'wrong-config', 'wrong-revision', 'wrong-token', 'unsafe-mode', 'journal-changed',
    'released-during-proof', 'active-stream', 'pointer-changed'])
def test_unbound_or_changed_hold_fails_closed(held, monkeypatch, fault):
    if fault == 'wrong-kind': held.record['operation']['kind'] = 'finalize-clients'
    elif fault == 'wrong-phase': held.record['phase'] = 'releasing'
    elif fault == 'wrong-project': held.record['project'] = 'other'
    elif fault == 'wrong-source': held.record['source'] = '/other/config'
    elif fault == 'wrong-config': held.record['selection']['sha256'] = 'f' * 64
    elif fault == 'wrong-revision': held.observed['revision'] = 'f' * 64
    elif fault == 'wrong-token': held.record['binding'] = dict(held.binding, token='f' * 64)
    held.write(held.journal, held.record)
    if fault == 'unsafe-mode': held.journal.chmod(0o644)
    if fault == 'journal-changed':
        original = installer._migration_edge_request
        def changed(*args):
            result = original(*args)
            held.write(held.journal, dict(held.record, phase='ready'))
            return result
        monkeypatch.setattr(installer, '_migration_edge_request', changed)
    elif fault == 'released-during-proof':
        monkeypatch.setattr(installer, '_migration_edge_request',
            lambda *_args: (_ for _ in ()).throw(AccessError('revision-conflict')))
    elif fault == 'active-stream':
        original = installer._migration_edge_request
        monkeypatch.setattr(installer, '_migration_edge_request',
            lambda *args: dict(original(*args), streams=1))
    elif fault == 'pointer-changed':
        original = installer._migration_edge_request
        def moved(*args):
            result = original(*args)
            held.write(held.pointer, {'directory': 'ingress-refresh-' + 'f' * 32})
            return result
        monkeypatch.setattr(installer, '_migration_edge_request', moved)
    with pytest.raises(installer.InstallError, match='native-initial-admission-not-ready'):
        installer._verify_initial_retained_hold(held.plan, 'e' * 64, held.observed)
