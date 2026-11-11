"""Protected CLI dispatch retains the selected code and original data directory.

The capsule's publisher/import/cold-discovery qualification is separate. These
controls exercise the actual parent argument routing without launching a peer.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_native_source_installer_recovery import access
import pixel_native_installer_entry as entry


@pytest.mark.parametrize('arguments,code_source,extra', [
    (['--source', '/reviewed/source', '--install'], '/reviewed/source', ['--install-dir', '/reviewed/source']),
    (['--source=/reviewed/source', '--install-dir=/owner/ods', '--install'], '/reviewed/source', []),
    (['migrate-native', '--source', '/reviewed/source', '--install-dir', '/owner/ods', '--activate'], '/reviewed/source', []),
    (['recover-runtime', '--owner', 'fixture', '--current-bundle-digest', 'b' * 64,
      '--bundle-digest', 'a' * 64], None, []),
])
def test_root_cli_enters_isolated_fixed_peer_preserving_original_install_directory(monkeypatch, arguments, code_source, extra):
    monkeypatch.setattr(access.sys, 'platform', 'darwin')
    monkeypatch.setattr(access.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(entry, 'active_support', lambda: None)
    monkeypatch.setattr(access._native_services, 'helper', lambda name:
        SimpleNamespace(select_python=lambda: Path('/qualified/python')) if name == 'ops-service'
        else pytest.fail('unexpected code producer'))
    root = entry.SUPPORT_ROOT / ('a' * 64)
    staged = dict(root=str(root), entry=str(root / entry.ENTRY), codeSha256='a' * 64, python='/qualified/python')
    def stage(source, *, python):
        assert Path(source) == (Path(code_source) if code_source else access.HERE.parents[2])
        assert python == Path('/qualified/python')
        return staged
    monkeypatch.setattr(entry, 'stage_support', stage)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(access.subprocess, 'run', run)
    original = list(arguments)
    assert access._protected_cli(arguments) == 7
    assert calls == [(['/qualified/python', '-I', '-B', staged['entry'], '--peer', 'access-install',
        '--', *original, *extra], dict(check=False, timeout=access.PROTECTED_CLI_SECONDS))]
    assert arguments == original


def test_protected_activation_bounds_nest_inside_their_process_timeouts():
    # The finish is nested in the locked Source transaction; both must expire
    # before the protected CLI child and the owner's activation subprocess.
    assert access.SOURCE_TRANSACTION_SECONDS >= access.SOURCE_FINISH_SECONDS + 900
    assert access.PROTECTED_CLI_SECONDS >= access.SOURCE_TRANSACTION_SECONDS + 900
    spec = importlib.util.spec_from_file_location('bootstrap_native_update', access.HERE / 'pixel-native-update.py')
    update = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(update)
    assert update.ACTIVATION_TIMEOUT_SECONDS >= access.PROTECTED_CLI_SECONDS


@pytest.mark.parametrize('arguments', [[], ['--help'], ['migrate-native', '--verify-preparation']])
def test_plan_and_help_do_not_stage_or_enter_support(monkeypatch, arguments):
    monkeypatch.setattr(access.sys, 'platform', 'darwin')
    monkeypatch.setattr(access.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(entry, 'stage_support', lambda *_args, **_kwargs: pytest.fail('read-only route must not stage'))
    assert access._protected_cli(arguments) is None


def test_existing_private_scope_cannot_select_a_different_peer(monkeypatch):
    monkeypatch.setattr(access.sys, 'platform', 'darwin')
    monkeypatch.setattr(access.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(entry, 'active_support', lambda:
        dict(root=str(access.HERE.parents[2]), codeSha256='a' * 64, peer='native-uninstall'))
    with pytest.raises(access.InstallError, match='support-peer-invalid'):
        access._protected_cli(['--source', '/reviewed/source', '--install'])
