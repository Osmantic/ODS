"""Exercise the real CLI guard with private fixtures; never inspect live state."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
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


def shell_function(path, name):
    return re.search(r'(?ms)^' + re.escape(name) + r'\(\) \{.*?^}', path.read_text()).group()


@pytest.mark.skipif(os.geteuid() == 0, reason='Exercises the non-root installer sudo path')
@pytest.mark.parametrize('action', ['stage', 'hold', 'copy', 'status', 'finish', 'rollback', 'downstream'])
def test_source_upgrade_never_succeeds_by_skipping_sudo(tmp_path, action):
    root = MODULE.parents[1]
    source = tmp_path / 'source'
    (source / 'bin').mkdir(parents=True)
    marker = tmp_path / 'executed'
    (source / 'bin/pixel_source_upgrade.py').write_text(
        'from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n')
    script = shell_function(root / 'installers/lib/sudo.sh', 'ods_sudo') + '\n'
    script += shell_function(root / 'installers/lib/pixel-host-install.sh', '_ods_pixel_source_upgrade')
    script += '\n_ods_pixel_source_upgrade "$1" fixture\n'
    result = subprocess.run(['/bin/bash', '-c', script, 'fixture', action],
        env={**os.environ, 'ODS_SUDO_AVAILABLE': 'false', 'INTERACTIVE': 'false',
             'SCRIPT_DIR': str(source), 'INSTALL_DIR': str(tmp_path / 'installed')},
        text=True, capture_output=True, timeout=5)
    assert result.returncode != 0
    assert result.stdout == ''
    assert 'source-upgrade-sudo-required' in result.stderr
    assert str(tmp_path) not in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize('reply', ['', 'private-fixture-value', 'a' * 64 + '\nprivate-fixture-value'])
def test_invalid_hold_reply_has_its_own_public_reason_and_never_copies(reply):
    source = (MODULE.parents[1] / 'installers/phases/06-directories.sh').read_text()
    start = source.index('                ODS_PIXEL_SOURCE_TRANSACTION="$(_ods_pixel_source_upgrade hold')
    end = source.index('                export ODS_PIXEL_SOURCE_TRANSACTION', start)
    fragment = source[start:end]
    harness = r'''
error() { printf '%s\n' "$*" >&2; }
_phase06_source_failed() { error "unexpected generic failure"; return 1; }
_ods_pixel_source_upgrade() { printf '%s' "$REPLY"; }
_phase06_pixel_owner=fixture
exercise() {
'''
    result = subprocess.run(['/bin/bash', '-c', harness + fragment + '\nprintf COPY_REACHED\n}\nexercise'],
        env={**os.environ, 'REPLY': reply}, text=True, capture_output=True, timeout=5)
    assert result.returncode != 0
    assert result.stdout == ''
    assert 'source-hold-response-invalid' in result.stderr
    assert 'private-fixture-value' not in result.stderr
    assert 'printed above' not in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason='Exercises the non-root installer sudo path')
def test_source_upgrade_phase_stops_before_owner_or_transition_when_sudo_unavailable(tmp_path):
    source = (MODULE.parents[1] / 'installers/phases/06-directories.sh').read_text()
    start = source.index('    if [[ "${ENABLE_PIXEL_RUNTIME:-false}" == "true"',
                         source.index('    _phase06_pixel_marker='))
    end = source.index('    unset _phase06_pixel_marker', start)
    marker = tmp_path / 'pixel-managed.json'
    marker.write_text('{}')
    harness = r'''
error() { printf '%s\n' "$*" >&2; }
ods_pixel_install_owner() { printf 'OWNER_REACHED\n' >&2; return 1; }
exercise() {
'''
    result = subprocess.run(['/bin/bash', '-c', harness + source[start:end] + '\n}\nexercise'],
        env={**os.environ, 'ENABLE_PIXEL_RUNTIME': 'true', 'ODS_SUDO_AVAILABLE': 'false',
             '_phase06_pixel_marker': str(marker)}, text=True, capture_output=True, timeout=5)
    assert result.returncode != 0
    assert 'source-upgrade-sudo-required' in result.stderr
    assert 'OWNER_REACHED' not in result.stderr
    assert marker.read_text() == '{}'


@pytest.mark.parametrize('helper_exists', [False, True])
def test_source_upgrade_wrapper_preserves_success_and_reports_missing_helper(tmp_path, helper_exists):
    source = tmp_path / 'source'
    (source / 'bin').mkdir(parents=True)
    if helper_exists:
        (source / 'bin/pixel_source_upgrade.py').write_text('print("a" * 64)\n')
    # Simulate only elevation; run the wrapper and fixture helper for real.
    script = 'ods_sudo() { "$@"; }\n'
    script += shell_function(MODULE.parents[1] / 'installers/lib/pixel-host-install.sh',
                             '_ods_pixel_source_upgrade')
    script += '\n_ods_pixel_source_upgrade hold fixture\n'
    result = subprocess.run(['/bin/bash', '-c', script],
        env={**os.environ, 'ODS_SUDO_AVAILABLE': 'true', 'SCRIPT_DIR': str(source),
             'INSTALL_DIR': str(tmp_path / 'installed')}, text=True, capture_output=True, timeout=5)
    if helper_exists:
        assert result.returncode == 0
        assert result.stdout == 'a' * 64 + '\n'
        assert result.stderr == ''
    else:
        assert result.returncode != 0
        assert result.stdout == ''
        assert 'source-upgrade-helper-unavailable' in result.stderr


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
