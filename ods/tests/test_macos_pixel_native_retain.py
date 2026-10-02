"""A base installer rerun keeps a proved native Pixel without initial setup."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'installers/macos/install-macos.sh'
SPEC = importlib.util.spec_from_file_location(
    'native_retain', ROOT / 'installers/macos/lib/pixel-native-retain.py')
retain = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retain)


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = tmp_path / 'ods'
    preparation = root / 'data/pixel-native/preparation'
    preparation.mkdir(parents=True)
    digest, services = 'a' * 64, 'b' * 64
    ref = '9f3b6ecd25db3ab51bef4091473d88ee5824bc3b'
    (preparation / 'preparation.json').write_text(json.dumps({
        'schemaVersion': 1, 'status': 'prepared',
        'phase': 'awaiting-protected-activation',
        'home': str(root / 'data/pixel-native/home'),
        'pixelSourceRef': ref, 'runtimeDigest': digest,
        'serviceDigest': services}))
    (preparation / 'activation.json').write_text(json.dumps({
        'schemaVersion': 1, 'status': 'ready', 'phase': 'services-ready',
        'runtimeDigest': digest, 'serviceDigest': services}))
    initial = retain.helper('pixel-native-install.py')
    for relative in initial.FRAGMENTS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('services: {}\n')
    bundle = tmp_path / 'protected' / digest
    bundle.mkdir(parents=True)
    (bundle / 'ods-service-binding.json').write_text('{}')
    owner = SimpleNamespace(pw_name='fixture-owner', pw_uid=os.getuid())
    checks = []
    access = SimpleNamespace(
        _launchd=SimpleNamespace(GATEWAY_PLIST=tmp_path / 'gateway.plist',
                                 GATEWAY_TARGET='system/com.ods.pixel-native-gateway'),
        _bundle=SimpleNamespace(INSTALL_ROOT=bundle.parent,
            verify=lambda path, expected_digest: checks.append(('runtime', path, expected_digest)),
            verify_service_binding=lambda path, expected: checks.append(('services', path, expected))),
        RUNTIME_CONFIG_ROOT=tmp_path / 'config',
        _source_gateway=lambda *_args: (
            {'UserName': owner.pw_name}, {'OPENCLAW_CONFIG_PATH': str(tmp_path / 'config')},
            None, None, bundle / 'node', bundle / 'runtime/openclaw.mjs'),
        _command=lambda _args: 'system/com.ods.pixel-native-gateway = {\n\tstate = running\n\tpid = 61766\n}',
        _source_runtime_config=lambda *_args: True)
    original_helper = retain.helper
    monkeypatch.setattr(retain, 'helper', lambda name:
        access if name == 'pixel-macos-access-install.py' else original_helper(name))
    monkeypatch.setattr(retain, 'custody_module', lambda: SimpleNamespace(
        verify_loaded_launchd_definition=lambda *_args: checks.append(('loaded', digest))))
    monkeypatch.setattr(retain.sys, 'platform', 'darwin')
    monkeypatch.setattr(retain.os, 'geteuid', lambda: 501)
    monkeypatch.setattr(retain.pwd, 'getpwuid', lambda _uid: owner)
    return root, preparation, digest, services, ref, checks


def test_retained_selection_matches_active_native_bundle(installed):
    root, _, digest, services, ref, checks = installed
    assert retain.verify(root, expected_ref=ref) == {
        'mode': 'retained', 'runtimeDigest': digest, 'serviceDigest': services}
    assert checks == [('loaded', digest),
                      ('runtime', root.parent / 'protected' / digest, digest),
                      ('services', root.parent / 'protected' / digest, services)]


@pytest.mark.parametrize('fault', ['partial', 'wrong-ref', 'digest-drift',
                                   'missing-fragment', 'missing-binding', 'stopped'])
def test_retained_preflight_refuses_unproved_state(installed, monkeypatch, fault):
    root, preparation, digest, services, ref, checks = installed
    if fault == 'partial':
        (preparation / 'activation.json').unlink()
    elif fault == 'wrong-ref':
        ref = 'c' * 40
    elif fault == 'digest-drift':
        original = retain.helper
        access = original('pixel-macos-access-install.py')
        access._source_gateway = lambda *_args: (
            {'UserName': 'fixture-owner'}, {}, None, None,
            root.parent / 'protected' / ('c' * 64) / 'node',
            root.parent / 'protected' / ('c' * 64) / 'runtime/openclaw.mjs')
    elif fault == 'missing-fragment':
        (root / 'installers/macos/pixel-native.compose.yaml.disabled').unlink()
    elif fault == 'stopped':
        retain.helper('pixel-macos-access-install.py')._command = lambda _args: (
            'system/com.ods.pixel-native-gateway = {\n\tstate = waiting\n}')
    else:
        (root.parent / 'protected' / digest / 'ods-service-binding.json').unlink()
    with pytest.raises((ValueError, OSError, FileNotFoundError)):
        retain.verify(root, expected_ref=ref)


def test_retained_preflight_refuses_symlinked_native_root(installed):
    root, *_ = installed
    native = root / 'data/pixel-native'
    native.rename(root / 'data/saved-native')
    native.symlink_to(root / 'data/saved-native', target_is_directory=True)
    with pytest.raises(ValueError, match='existing-native-pixel-needs-review'):
        retain.verify(root)


@pytest.mark.parametrize('retained', [False, True])
def test_shell_dispatches_fresh_or_retained_before_phase_one(tmp_path, retained):
    script = SCRIPT.read_text()
    start = script.index('if $ENABLE_PIXEL && ! $PREFLIGHT_ONLY; then')
    stop = script.index('\nif ! $OPENCLAW_EXPLICIT; then', start)
    assert start < script.index('# PHASE 1')
    body = script[start:stop].replace('/usr/bin/python3', 'python_fixture')
    if retained:
        (tmp_path / 'data/pixel-native').mkdir(parents=True)
    shell = '''set -euo pipefail
python_fixture() { printf '%s\n' "$1"; }
''' + body + '\n'
    result = subprocess.run(['bash'], input=shell, capture_output=True, text=True,
        env={**os.environ, 'ENABLE_PIXEL': 'true', 'PREFLIGHT_ONLY': 'false',
             'NON_INTERACTIVE': 'true', 'DRY_RUN': 'false',
             'PIXEL_SOURCE_REF': '', 'INSTALL_DIR': str(tmp_path), 'LIB_DIR': '/source/lib'},
        check=True)
    assert result.stdout.splitlines() == [
        '/source/lib/pixel-native-retain.py' if retained else '/source/lib/pixel-native-install.py']


def test_retained_path_does_not_call_initial_setup_after_base_launch():
    script = SCRIPT.read_text()
    start = script.index('    if $ENABLE_PIXEL; then\n        if $_PIXEL_RETAINED; then')
    stop = script.index('\n    # Save compose flags for ods-macos.sh', start)
    body = script[start:stop].replace('/usr/bin/python3', 'python_fixture')
    shell = '''set -euo pipefail
python_fixture() { printf '%s\n' "$1"; }
ai_ok() { :; }
ai_err() { echo "$*" >&2; }
COMPOSE_FLAGS=(-f docker-compose.base.yml)
_pixel_retain_args=(--install-dir /owner/ods)
_PIXEL_RETAINED=true
ENABLE_PIXEL=true
''' + body + '\n'
    result = subprocess.run(['bash'], input=shell, capture_output=True, text=True,
        env={**os.environ, 'LIB_DIR': '/source/lib'}, check=True)
    assert result.stdout.splitlines() == ['/source/lib/pixel-native-retain.py']
    assert result.stderr == ''
