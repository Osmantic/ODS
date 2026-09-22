"""Real macOS kernel checks over disposable, user-owned filesystem fixtures."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from pixel_macos_policy import PolicyError, render_policy
import pixel_macos_policy as policy
import pixel_macos_custody as custody


@pytest.fixture
def policy_store(tmp_path, monkeypatch):
    """User-owned storage: mock custody only, never claim privileged validation."""
    receipt = policy.deployment_receipt(tmp_path.resolve(), {
        'sandboxed': b'approved sandboxed', 'full-access': b'approved full-access'})
    for mode, record in receipt['profiles'].items():
        Path(record['path']).write_bytes(('approved ' + mode).encode())
    Path(receipt['active']).write_bytes(b'approved sandboxed')
    monkeypatch.setattr(custody, 'protected_bytes', lambda path: Path(path).read_bytes())
    monkeypatch.setattr(policy.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(policy.os, 'fchown', lambda *args: None)
    return receipt


def test_selection_roundtrip_preserves_approved_profiles(policy_store):
    receipt = policy_store
    originals = {mode: Path(record['path']).read_bytes() for mode, record in receipt['profiles'].items()}
    for mode in ('full-access', 'full-access', 'sandboxed'):
        policy.select_policy(receipt, mode)
        assert policy.policy_state(receipt)['activeMode'] == mode
    assert originals == {mode: Path(record['path']).read_bytes() for mode, record in receipt['profiles'].items()}
    assert not list(Path(receipt['active']).parent.glob('.ods-policy-*'))


@pytest.mark.parametrize('target', ['active', 'sandboxed', 'full-access'])
def test_changed_profile_fails_without_repair(policy_store, target):
    receipt = policy_store
    path = receipt['active'] if target == 'active' else receipt['profiles'][target]['path']
    Path(path).write_bytes(b'changed deployment')
    before = Path(receipt['active']).read_bytes()
    with pytest.raises(PolicyError, match='unapproved-active|approved-policy-changed'):
        policy.select_policy(receipt, 'full-access')
    assert Path(receipt['active']).read_bytes() == before


def test_missing_profile_is_not_regenerated(policy_store):
    Path(policy_store['profiles']['full-access']['path']).unlink()
    with pytest.raises(OSError):
        policy.select_policy(policy_store, 'full-access')
    assert Path(policy_store['active']).read_bytes() == b'approved sandboxed'


def test_policy_requires_custody_even_for_noop(policy_store, monkeypatch):
    def reject(_):
        raise custody.CustodyError('untrusted file')
    monkeypatch.setattr(custody, 'protected_bytes', reject)
    with pytest.raises(PolicyError, match='policy-custody-required'):
        policy.select_policy(policy_store, 'sandboxed')


def test_nonroot_policy_selection_cannot_write(policy_store, monkeypatch):
    monkeypatch.setattr(policy.os, 'geteuid', lambda: 501)
    with pytest.raises(PolicyError, match='root-policy-controller-required'):
        policy.select_policy(policy_store, 'full-access')


@pytest.mark.parametrize('change', ['same-hash', 'path-traversal', 'other-directory', 'unknown-mode', 'bool-schema'])
def test_receipt_rejects_ambiguity(policy_store, change):
    receipt = policy_store
    if change == 'same-hash':
        receipt['profiles']['full-access']['sha256'] = receipt['profiles']['sandboxed']['sha256']
    elif change == 'path-traversal':
        receipt['active'] = '/private/etc/../ods/pixel-gateway.sb'
    elif change == 'other-directory':
        receipt['profiles']['full-access']['path'] = '/other/pixel-gateway.full-access.sb'
    elif change == 'unknown-mode':
        receipt['profiles']['unknown'] = receipt['profiles']['sandboxed']
    else:
        receipt['schemaVersion'] = True
    with pytest.raises(PolicyError):
        policy.policy_state(receipt)


def test_failed_publish_leaves_previous_profile_and_no_temp(policy_store, monkeypatch):
    def fail(*_):
        raise OSError('simulated publication failure')
    monkeypatch.setattr(policy.os, 'replace', fail)
    with pytest.raises(OSError):
        policy.select_policy(policy_store, 'full-access')
    assert policy.policy_state(policy_store)['activeMode'] == 'sandboxed'
    assert not list(Path(policy_store['active']).parent.glob('.ods-policy-*'))


def test_concurrent_selection_is_rejected_before_publish(policy_store, monkeypatch):
    original = policy._deployment
    calls = 0
    def changed(receipt):
        nonlocal calls
        calls += 1
        if calls == 2:
            Path(receipt['active']).write_bytes(b'approved full-access')
        return original(receipt)
    monkeypatch.setattr(policy, '_deployment', changed)
    with pytest.raises(PolicyError, match='policy-selection-changed'):
        policy.select_policy(policy_store, 'full-access')
    assert not list(Path(policy_store['active']).parent.glob('.ods-policy-*'))


@pytest.mark.parametrize('path', ['/', 'relative', '/a/../b', '/a//b', '/a\nb'])
def test_policy_rejects_ambiguous_paths(path):
    with pytest.raises(PolicyError):
        render_policy(mode='sandboxed', writable=[path], protected=['/opt/pixel'], probe='/private/probe')


def test_policy_rejects_writing_inside_a_protected_runtime():
    with pytest.raises(PolicyError, match='mutable-path-inside'):
        render_policy(mode='full-access', writable=['/opt/pixel/state'],
                      protected=['/opt/pixel'], probe='/private/probe')


@pytest.fixture
def policy_fixture(tmp_path):
    if sys.platform != 'darwin':
        pytest.skip('requires the macOS Seatbelt kernel')
    root = tmp_path.resolve()
    runtime = root / 'operational/runtime'
    state = root / 'operational/state'
    outside = root / 'user-files'
    probe = root / 'probe'
    for path in (runtime, state, outside, probe):
        path.mkdir(parents=True)
    (runtime / 'entrypoint').write_text('original-runtime')
    (outside / 'private').write_text('owner-file')
    (state / 'runtime-link').symlink_to(runtime, target_is_directory=True)
    (state / 'outside-link').symlink_to(outside, target_is_directory=True)
    def launch(mode, command, *args):
        profile = root / (mode + '.sb')
        profile.write_bytes(render_policy(mode=mode, writable=[root / 'operational'],
                                         protected=[runtime], probe=probe))
        return subprocess.run(['/usr/bin/sandbox-exec', '-f', str(profile), '/bin/sh', '-c', command,
                               'policy-test', *map(str, args)], cwd='/', capture_output=True,
                              text=True, timeout=10, env={'PATH': '/usr/bin:/bin'})
    return root, runtime, state, outside, probe, launch


@pytest.mark.parametrize('mode', ['sandboxed', 'full-access'])
def test_runtime_readable_but_immutable_in_both_modes(policy_fixture, mode):
    root, runtime, state, _, _, launch = policy_fixture
    read = launch(mode, '/bin/cat "$1"', runtime / 'entrypoint')
    assert read.returncode == 0, read.stderr
    assert read.stdout == 'original-runtime'
    for target in (runtime / 'entrypoint', state / 'runtime-link/entrypoint'):
        result = launch(mode, 'printf changed > "$1"', target)
        assert result.returncode != 0, 'protected runtime write unexpectedly succeeded'
    rename = launch(mode, '/bin/mv "$1" "$2"', root / 'operational', root / 'moved')
    assert rename.returncode != 0
    assert (runtime / 'entrypoint').read_text() == 'original-runtime'
    assert launch(mode, 'printf state > "$1"', state / 'created').returncode == 0


@pytest.mark.parametrize('mode', ['sandboxed', 'full-access'])
def test_mode_changes_host_files_and_probe_write(policy_fixture, mode):
    _, _, state, outside, probe, launch = policy_fixture
    allowed = mode == 'full-access'
    for target in (outside / 'created', state / 'outside-link/aliased', probe / 'sentinel'):
        result = launch(mode, 'printf allowed > "$1"', target)
        assert (result.returncode == 0) == allowed, result.stderr
        assert target.exists() == allowed
    result = launch(mode, '/bin/cat "$1"', outside / 'private')
    assert (result.returncode == 0) == allowed, result.stderr


def test_protected_file_cannot_be_modified_via_new_hardlink(policy_fixture):
    _, runtime, _, outside, _, launch = policy_fixture
    result = launch('full-access', '/bin/ln "$1" "$2" && printf changed > "$2"',
                    runtime / 'entrypoint', outside / 'linked')
    assert result.returncode != 0, 'hardlink bypassed runtime protection'
    assert (runtime / 'entrypoint').read_text() == 'original-runtime'


def test_selected_profile_only_applies_to_new_process(policy_fixture, monkeypatch):
    root, runtime, state, outside, probe, _ = policy_fixture
    profiles = {mode: render_policy(mode=mode, writable=[root / 'operational'],
                                   protected=[runtime], probe=probe) for mode in policy.MODES}
    receipt = policy.deployment_receipt(root, profiles)
    for mode, record in receipt['profiles'].items():
        Path(record['path']).write_bytes(profiles[mode])
    Path(receipt['active']).write_bytes(profiles['sandboxed'])
    # Kernel isolation is real; this fixture does not exercise root custody.
    monkeypatch.setattr(custody, 'protected_bytes', lambda path: Path(path).read_bytes())
    monkeypatch.setattr(policy.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(policy.os, 'fchown', lambda *args: None)
    # The process below stays alive across selection.
    command = ['/usr/bin/sandbox-exec', '-f', receipt['active'], '/bin/sh', '-c',
               'printf "ready\\n"; read line; printf changed > "$1"',
               'policy-test', str(outside / 'old-process-write')]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, cwd='/', env={'PATH': '/usr/bin:/bin'})
    try:
        import selectors
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=5), 'sandboxed process did not become ready'
        assert process.stdout.readline() == 'ready\n'
        policy.select_policy(receipt, 'full-access')
        assert policy.policy_state(receipt)['activeMode'] == 'full-access'
        _, error = process.communicate('continue\n', timeout=5)
        assert process.returncode != 0, error
        assert not (outside / 'old-process-write').exists()
        fresh = subprocess.run(['/usr/bin/sandbox-exec', '-f', receipt['active'],
                                '/bin/sh', '-c', 'printf changed > "$1"',
                                'policy-test', str(outside / 'new-process-write')],
                               capture_output=True, timeout=5, cwd='/')
        assert fresh.returncode == 0, fresh.stderr
        assert (outside / 'new-process-write').read_text() == 'changed'
        policy.select_policy(receipt, 'sandboxed')
        restored = subprocess.run(['/usr/bin/sandbox-exec', '-f', receipt['active'],
                                   '/bin/sh', '-c', 'printf changed > "$1"',
                                   'policy-test', str(outside / 'restored-process-write')],
                                  capture_output=True, timeout=5, cwd='/')
        assert restored.returncode != 0
        assert not (outside / 'restored-process-write').exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
