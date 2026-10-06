"""Exercise the real CLI guard with private fixtures; never inspect live state."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import pwd
import stat
import sys
import tempfile
from types import SimpleNamespace

import pytest


MODULE = Path(__file__).resolve().parents[1] / 'bin/pixel_source_upgrade.py'
spec = importlib.util.spec_from_file_location('source_upgrade_diagnostics', MODULE)
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)
ENTRY = compile(ast.Module(body=[ast.parse(MODULE.read_text()).body[-1]], type_ignores=[]),
                str(MODULE), 'exec')


def refuse(*args, **kwargs):
    pytest.fail('fixture attempted an unapproved write or live coordinator call')


def cli(monkeypatch, capsys, args):
    # Execute the production __main__ block, including its real exception
    # handling and stderr output, with only host boundaries redirected below.
    monkeypatch.setattr(sys, 'argv', [str(MODULE), *args])
    namespace = {**upgrade.__dict__, '__name__': '__main__'}
    with pytest.raises(SystemExit) as result:
        exec(ENTRY, namespace)
    assert result.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'keep admission held' not in captured.err
    assert 'resume the same reviewed installer' not in captured.err
    return captured.err


def snapshot(root):
    return {str(path.relative_to(root)): (
        stat.S_IMODE(path.lstat().st_mode), path.lstat().st_ino,
        ('link', os.readlink(path)) if path.is_symlink()
        else ('directory',) if path.is_dir() else ('file', path.read_bytes()))
        for path in root.rglob('*')}


@pytest.fixture
def private_root():
    # Real ancestor-custody checks reject /tmp. The test runner supplies a
    # private HOME; no installed owner state is read, modified, or removed.
    with tempfile.TemporaryDirectory(prefix='.source-diagnostics-', dir=Path.home()) as directory:
        yield Path(directory)


@pytest.mark.parametrize('marker_state', ['installing', 'ready'])
@pytest.mark.parametrize('condition,reason', [
    ('missing-state', 'coordinator state is missing'),
    ('missing-config', 'coordinator configuration is missing'),
    ('unsafe-state', None),
    ('symlink-config', None),
    ('stale-config', None),
])
def test_manager_cli_refusals_preserve_all_fixture_state(
        private_root, monkeypatch, capsys, marker_state, condition, reason):
    root = private_root
    install, home, state, config = (root / name for name in ('installed', 'owner', 'access-state', 'access.json'))
    install.mkdir(mode=0o700)
    home.mkdir(mode=0o700)
    (home / 'marker.json').write_text(json.dumps(dict(state=marker_state, pixel_source_ref='a' * 40)))
    (home / 'retained-data.txt').write_text('private owner data')
    (install / 'source.py').write_text('retained source bytes')
    if condition != 'missing-state':
        state.mkdir(mode=0o700)
    if condition == 'unsafe-state':
        state.chmod(0o755)
    if condition != 'missing-config':
        config.write_text(json.dumps(dict(install_dir=str(install) if condition != 'stale-config' else '/wrong',
                                         owner='fixture')))
        config.chmod(0o600)
    if condition == 'symlink-config':
        target = config.with_name('retained-config.json')
        config.rename(target)
        config.symlink_to(target)
    original_directory, original_json = upgrade.directory, upgrade._protected_json
    def fixture_path(value):
        return {'/var/lib/ods-pixel-access': state, '/etc/ods/pixel-access.json': config}.get(str(value), Path(value))
    monkeypatch.setattr(upgrade, 'Path', fixture_path)
    # Root identity is simulated only at the syscall/custody boundary. Actual
    # no-follow reads, modes, JSON parsing and install binding still execute.
    monkeypatch.setattr(upgrade.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(home)))
    monkeypatch.setattr(upgrade, 'directory', lambda path, uid, **kw: original_directory(path, os.getuid(), **kw))
    monkeypatch.setattr(upgrade, '_protected_json', lambda path, uid=0: original_json(path, os.getuid()))
    monkeypatch.setattr(upgrade, 'SourceUpgrade', refuse)
    monkeypatch.setattr(upgrade, '_client', refuse)
    before = snapshot(root)
    message = cli(monkeypatch, capsys, ['stage', str(install), 'fixture', str(root / 'candidate'), 'b' * 40])
    assert snapshot(root) == before
    assert not (state / 'source-upgrade').exists()
    assert str(root) not in message
    if reason:
        assert reason in message
        assert 'Restore the complete matching prior installation state' in message
        assert 'owner-authorized clean install' in message
    else:
        assert 'is missing' not in message
        assert 'Preserve any existing admission hold' in message


def test_nonready_baseline_cli_preserves_verified_marker_and_source(private_root, monkeypatch, capsys):
    root = private_root
    install, home, candidate, state = (root / name for name in ('installed', 'owner', 'candidate', 'state'))
    for path in (install, home, candidate, state):
        path.mkdir(mode=0o700)
    marker = home / '.config/ods/pixel-managed.json'
    marker.parent.mkdir(parents=True, mode=0o700)
    (home / '.config').chmod(0o700)
    marker.write_text(json.dumps(dict(schema_version=2, manager='ods', install_dir=str(install),
                                      initial_active_state='absent', state='installing', pixel_source_ref='a' * 40,
                                      active_release_version='4.3.27', configuration_sha256='c' * 64)))
    marker.chmod(0o600)
    (home / 'retained-data').write_bytes(b'owner data')
    (install / 'source.py').write_bytes(b'prior source')
    for service in ('pixel-edge', 'litellm'):
        path = candidate / f'extensions/services/{service}/compose.yaml'
        path.parent.mkdir(parents=True)
        path.write_text('services: {}')
    manager = SimpleNamespace(state=state / 'source-upgrade', install=install,
                              journal=lambda: None, stage=refuse)
    owner = SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(home))
    original_json = upgrade._protected_json
    monkeypatch.setattr(upgrade, '_protected_json', lambda path, uid=0: original_json(path, os.getuid()))
    monkeypatch.setattr(upgrade, '_manager', lambda *a, **kw: (manager, owner, state))
    # Admission-lock acquisition is replaced; all stage marker validation is real.
    import contextlib
    monkeypatch.setattr(upgrade, 'admission_lock', lambda _: contextlib.nullcontext())
    monkeypatch.setattr(upgrade, 'owner_baseline', refuse)
    monkeypatch.setattr(upgrade, '_client', refuse)
    before = snapshot(root)
    message = cli(monkeypatch, capsys, ['stage', str(install), 'fixture', str(candidate), 'b' * 40])
    assert 'no ready source-upgrade baseline' in message
    assert 'owner-authorized clean install' in message
    assert snapshot(root) == before


@pytest.mark.parametrize('error', [
    upgrade.UpgradeError('source-hold-unconfirmed'),
    upgrade.UpgradeError('source-owner-state-changed'),
    upgrade.UpgradeError('source-live-drift'),
    OSError('/private/configuration-value'),
    ValueError('/private/snapshot-payload'),
])
def test_other_cli_failures_preserve_hold_guidance_without_leaking_exception(monkeypatch, capsys, error):
    def fail(_):
        raise error
    monkeypatch.setattr(upgrade, 'main', fail)
    message = cli(monkeypatch, capsys, ['copy', '/fixture', 'fixture'])
    assert 'Preserve any existing admission hold and protected source snapshots' in message
    assert '/private/' not in message
    assert 'clean install' not in message
    if isinstance(error, upgrade.UpgradeError):
        # A fixed code is source text, not private data; naming it says what
        # blocks the update (fleet row 27 hid model-recovery-required).
        assert f'(reason: {error})' in message
    else:
        assert str(error) not in message and '(reason:' not in message


def test_cli_mismatched_hold_preserves_actual_journal_and_snapshots(private_root, monkeypatch, capsys):
    root = private_root
    old, new, state = (root / name for name in ('old', 'new', 'state'))
    for path in (old, new, state):
        path.mkdir(mode=0o700)
    for path in (old, new):
        for name in upgrade.ROOTS:
            (path / name).mkdir()
        (path / 'bin/program.py').write_text(path.name)
    (state / 'source-upgrade').mkdir(mode=0o700)
    manager = upgrade.SourceUpgrade(state / 'source-upgrade', old, os.getuid(), state_uid=os.getuid())
    identity = dict(beforeRef='a' * 40, afterRef='b' * 40, markerSha256='c' * 64,
                    configSha256='e' * 64, receiptSha256=None)
    manager.stage(new, os.getuid(), identity)
    manager.bind('d' * 64, lambda _: None)
    pending = state / 'transition.json'
    pending.write_text(json.dumps(dict(kind='model', transaction_id='f' * 64,
                                       phase='held', configured_mode='sandboxed', token='a' * 64)))
    pending.chmod(0o600)
    original_json = upgrade._protected_json
    monkeypatch.setattr(upgrade, '_protected_json', lambda path, uid=0: original_json(path, os.getuid()))
    monkeypatch.setattr(upgrade, '_manager', lambda *a, **kw: (manager, None, state))
    monkeypatch.setattr(upgrade, '_client', lambda: refuse)
    before = snapshot(root)
    message = cli(monkeypatch, capsys, ['copy', str(old), 'fixture'])
    assert 'Preserve any existing admission hold and protected source snapshots' in message
    assert snapshot(root) == before
    assert manager.journal()['hold'] == 'd' * 64


@pytest.mark.parametrize('error,shown', [
    (upgrade.UpgradeError('source-completion-required'), '(reason: source-completion-required)'),
    (RuntimeError('model-hold-unconfirmed'), '(reason: model-hold-unconfirmed)'),
    (RuntimeError('text with spaces and /a/private/path'), None),
    (OSError(2, 'No such file or directory', '/a/private/path'), None),
    (ValueError('invalid model transition request'), None),
])
def test_incomplete_update_names_only_fixed_reason_codes(error, shown):
    # Fleet, laptop 2026-10-05: every refusal read only "Pixel source upgrade
    # is incomplete", which hid the coordinator's own reason.
    message = upgrade._failure_message(error)
    assert 'Preserve any existing admission hold' in message
    assert '/a/private/path' not in message
    if shown:
        assert shown in message
    else:
        assert '(reason:' not in message
