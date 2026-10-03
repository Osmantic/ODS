"""The privileged repair binds a legacy bundle to the running native selection."""
import importlib.util
import os
from pathlib import Path
import pwd
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'native_link_repair', ROOT / 'installers/macos/lib/pixel-native-link-repair.py')
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


@pytest.fixture
def active(tmp_path, monkeypatch):
    owner = pwd.getpwuid(os.getuid())
    if owner.pw_uid == 0:
        pytest.skip('requires an unprivileged fixture owner')
    root = tmp_path / 'ods'
    preparation = root / 'data/pixel-native/preparation'
    preparation.mkdir(parents=True)
    digest, services = 'a' * 64, 'b' * 64
    bundle = tmp_path / 'protected' / digest
    checks = []
    stack = SimpleNamespace(
        resolve_files=lambda *_args: checks.append('stack'),
        read_selection=lambda _path: (
            {'runtimeDigest': digest, 'serviceDigest': services},
            {'runtimeDigest': digest, 'serviceDigest': services}))
    runtime = SimpleNamespace(
        INSTALL_ROOT=bundle.parent,
        verify_service_binding=lambda path, selected: checks.append(('binding', path, selected)),
        repair_legacy_link_modes=lambda selected: checks.append(('repair', selected)) or
            {'digest': selected, 'linksRepaired': 1})
    gateway = SimpleNamespace(
        _launchd=SimpleNamespace(GATEWAY_PLIST=tmp_path / 'gateway.plist',
            GATEWAY_TARGET='system/com.ods.pixel-native-gateway'),
        _bundle=runtime, RUNTIME_CONFIG_ROOT=tmp_path / 'config',
        _source_gateway=lambda *_args: (
            {'UserName': owner.pw_name}, {'OPENCLAW_CONFIG_PATH': str(tmp_path / 'config')},
            None, None, bundle / 'node', bundle / 'runtime/openclaw.mjs'),
        _source_runtime_config=lambda *_args: True,
        _command=lambda _args: ('system/com.ods.pixel-native-gateway = {\n'
            '\tstate = running\n\tpid = 61766\n}'))
    retained = SimpleNamespace(protected_clear=lambda: checks.append('protected-clear'))
    modules = {'pixel-native-retain.py': retained, 'pixel-native-stack.py': stack,
               'pixel-macos-access-install.py': gateway}
    monkeypatch.setattr(repair, 'helper', lambda filename: modules[filename])
    monkeypatch.setattr(repair, 'custody_module', lambda: SimpleNamespace(
        verify_loaded_launchd_definition=lambda *_args: checks.append('loaded')))
    monkeypatch.setattr(repair.sys, 'platform', 'darwin')
    monkeypatch.setattr(repair.os, 'geteuid', lambda: 0)
    return root, digest, services, owner, checks, stack, gateway


def test_repair_requires_running_bound_selection(active):
    root, digest, services, owner, checks, _, _ = active
    assert repair.repair(root, owner.pw_uid) == {'digest': digest, 'linksRepaired': 1}
    assert checks == ['protected-clear', 'stack', 'loaded',
                      ('binding', root.parent / 'protected' / digest, services),
                      ('repair', digest)]


def test_repair_refuses_digest_not_selected_by_loaded_gateway(active):
    root, digest, _, owner, checks, _, gateway = active
    gateway._source_gateway = lambda *_args: (
        {'UserName': owner.pw_name}, {'OPENCLAW_CONFIG_PATH': str(root.parent / 'config')},
        None, None, root.parent / 'protected' / ('c' * 64) / 'node',
        root.parent / 'protected' / ('c' * 64) / 'runtime/openclaw.mjs')
    with pytest.raises(ValueError, match='installed-native-selection-drift'):
        repair.repair(root, owner.pw_uid)
    assert ('repair', digest) not in checks


def test_repair_refuses_pending_protected_transition(active):
    root, digest, _, owner, checks, _, _ = active
    original = repair.helper
    modules = {'pixel-native-retain.py': SimpleNamespace(
        protected_clear=lambda: (_ for _ in ()).throw(ValueError('pending-protected-journal')))}
    repair.helper = lambda name: modules.get(name) or original(name)
    try:
        with pytest.raises(ValueError, match='pending-protected-journal'):
            repair.repair(root, owner.pw_uid)
    finally:
        repair.helper = original
    assert ('repair', digest) not in checks
