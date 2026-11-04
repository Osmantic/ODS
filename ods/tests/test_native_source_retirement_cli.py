"""Retirement CLI enters protected code without rewriting owner data paths."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
spec = importlib.util.spec_from_file_location('retirement_cli_dispatch', ROOT / 'installers/macos/lib/pixel-native-uninstall.py')
retirement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retirement)
import pixel_native_installer_entry as entry


@pytest.mark.parametrize('platform,uid,args', [('linux', 0, []), ('darwin', 501, []),
    ('darwin', 0, ['--help']), ('darwin', 0, ['-h'])])
def test_readonly_or_unprivileged_cli_never_stages_code(monkeypatch, platform, uid, args):
    monkeypatch.setattr(retirement.sys, 'platform', platform)
    monkeypatch.setattr(retirement.os, 'geteuid', lambda: uid)
    monkeypatch.setattr(entry, 'stage_support', lambda *_a, **_k: pytest.fail('no publication'))
    assert retirement._protected_cli(args) is None


def test_cli_uses_fresh_protected_peer_and_preserves_custom_data_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(retirement.sys, 'platform', 'darwin')
    monkeypatch.setattr(retirement.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(entry, 'active_support', lambda: None)
    root = tmp_path / 'support'
    monkeypatch.setattr(entry, 'SUPPORT_ROOT', root)
    support = dict(root=str(root / ('a' * 64)), entry=str(root / ('a' * 64) / entry.ENTRY),
        codeSha256='a' * 64, python='/qualified/root/python')
    calls = []
    def stage(source, *, python):
        calls.append(('stage', source, python))
        return support
    monkeypatch.setattr(entry, 'stage_support', stage)
    ops = SimpleNamespace(select_python=lambda: support['python'])
    services = SimpleNamespace(helper=lambda name: ops if name == 'ops-service' else pytest.fail('unknown dependency'))
    monkeypatch.setattr(retirement, 'helper', lambda name: services if name == 'pixel-native-services' else pytest.fail('unknown helper'))
    def run(argv, **kwargs):
        calls.append(('run', argv, kwargs))
        return SimpleNamespace(returncode=19)
    monkeypatch.setattr(retirement.subprocess, 'run', run)
    arguments = ['--owner', 'fixture-owner', '--install-dir', '/Users/fixture-owner/custom ODS', '--validate-only']
    original = list(arguments)
    assert retirement._protected_cli(arguments) == 19
    assert arguments == original
    assert calls == [('stage', retirement.HERE.parents[2], support['python']), ('run',
        [support['python'], '-I', '-B', support['entry'], '--peer', 'native-uninstall', '--', *original],
        dict(check=False, timeout=1800))]


@pytest.mark.parametrize('peer,root_ok', [('native-uninstall', True), ('access-install', True), ('native-uninstall', False)])
def test_only_the_same_protected_peer_avoids_recursive_entry(monkeypatch, peer, root_ok):
    monkeypatch.setattr(retirement.sys, 'platform', 'darwin')
    monkeypatch.setattr(retirement.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(entry, 'active_support', lambda: dict(peer=peer,
        root=str(retirement.HERE.parents[2]) if root_ok else '/unrelated/root'))
    monkeypatch.setattr(entry, 'stage_support', lambda *_a, **_k: pytest.fail('no recursive staging'))
    if peer == 'native-uninstall' and root_ok:
        assert retirement._protected_cli(['--owner', 'owner']) is None
    else:
        with pytest.raises(ValueError, match='support-peer-invalid'):
            retirement._protected_cli(['--owner', 'owner'])
