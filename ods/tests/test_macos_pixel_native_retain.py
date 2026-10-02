"""A base installer rerun keeps a proved native Pixel without initial setup."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import stat
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
    source = tmp_path / 'source'
    source.mkdir()
    service_relative = 'extensions/services/pixel-agent/host/extension_manager.py'
    source_file = source / service_relative
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b'current service payload\n')
    manifest_body = json.dumps({'candidateConfigSha256': 'c' * 64}).encode()
    service_bundle = preparation / 'services'
    service_bundle.mkdir()
    (service_bundle / 'services.json').write_bytes(manifest_body)
    digest, services = 'a' * 64, hashlib.sha256(manifest_body).hexdigest()
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
    def record(body):
        return {'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}
    selected_manifest = {'files': {
        'manager/extension_manager.py': record(b'current service payload\n'),
        'helpers/extension-catalog.json': record(b'current catalog\n')}}
    owner = SimpleNamespace(pw_name='fixture-owner', pw_uid=os.getuid())
    checks = []
    access = SimpleNamespace(
        _launchd=SimpleNamespace(GATEWAY_PLIST=tmp_path / 'gateway.plist',
                                 GATEWAY_TARGET='system/com.ods.pixel-native-gateway'),
        _bundle=SimpleNamespace(INSTALL_ROOT=bundle.parent,
            verify=lambda path, expected_digest: checks.append(('runtime', path, expected_digest)),
            verify_service_binding=lambda path, expected: checks.append(('services', path, expected)),
            expected_release_selection=lambda path, expected_digest: {
                'serviceBundleDigest': services, 'pixelSourceRevision': ref,
                'serviceManifest': selected_manifest},
            validate_service_manifest_provenance=lambda _manifest: None),
        RUNTIME_CONFIG_ROOT=tmp_path / 'config',
        _source_gateway=lambda *_args: (
            {'UserName': owner.pw_name}, {'OPENCLAW_CONFIG_PATH': str(tmp_path / 'config')},
            None, None, bundle / 'node', bundle / 'runtime/openclaw.mjs'),
        _command=lambda _args: 'system/com.ods.pixel-native-gateway = {\n\tstate = running\n\tpid = 61766\n}',
        _source_runtime_config=lambda *_args: True)
    original_helper = retain.helper
    config = SimpleNamespace(SERVICE_SOURCES={'manager/extension_manager.py': service_relative},
        service_snapshot=lambda base, name, private=False: (Path(base) / name).read_bytes(),
        service_catalog=lambda _source: b'current catalog\n')
    monkeypatch.setattr(retain, 'helper', lambda name:
        access if name == 'pixel-macos-access-install.py' else
        config if name == 'pixel-native-config.py' else original_helper(name))
    monkeypatch.setattr(retain, 'require_protected_clear', lambda **_kwargs: None)
    monkeypatch.setattr(retain, 'custody_module', lambda: SimpleNamespace(
        verify_loaded_launchd_definition=lambda *_args: checks.append(('loaded', digest))))
    monkeypatch.setattr(retain.sys, 'platform', 'darwin')
    monkeypatch.setattr(retain.os, 'geteuid', lambda: 501)
    monkeypatch.setattr(retain.pwd, 'getpwuid', lambda _uid: owner)
    return root, preparation, digest, services, ref, checks, source


def test_retained_selection_matches_active_native_bundle(installed):
    root, _, digest, services, ref, checks, source = installed
    assert retain.verify(root, expected_ref=ref, ods_source=source) == {
        'mode': 'retained', 'runtimeDigest': digest, 'serviceDigest': services}
    assert checks == [('loaded', digest),
                      ('runtime', root.parent / 'protected' / digest, digest),
                      ('services', root.parent / 'protected' / digest, services)]


@pytest.mark.parametrize('fault', ['partial', 'wrong-ref', 'digest-drift',
                                   'missing-fragment', 'missing-binding', 'stopped'])
def test_retained_preflight_refuses_unproved_state(installed, monkeypatch, fault):
    root, preparation, digest, services, ref, checks, source = installed
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
        retain.verify(root, expected_ref=ref, ods_source=source)


def test_retained_preflight_refuses_symlinked_native_root(installed):
    root, *_, source = installed
    native = root / 'data/pixel-native'
    native.rename(root / 'data/saved-native')
    native.symlink_to(root / 'data/saved-native', target_is_directory=True)
    with pytest.raises(ValueError, match='existing-native-pixel-needs-review'):
        retain.verify(root, ods_source=source)


def test_retained_preflight_refuses_changed_current_service_source(installed):
    root, preparation, digest, services, ref, checks, source = installed
    (source / 'extensions/services/pixel-agent/host/extension_manager.py').write_bytes(b'new payload\n')
    with pytest.raises(retain.SourceUpdateRequired, match='native-service-source-changed'):
        retain.verify(root, expected_ref=ref, ods_source=source)


def test_retained_preflight_refuses_changed_current_catalog(installed):
    root, preparation, digest, services, ref, checks, source = installed
    retain.helper('pixel-native-config.py').service_catalog = lambda _source: b'new catalog\n'
    with pytest.raises(retain.SourceUpdateRequired, match='native-service-catalog-changed'):
        retain.verify(root, expected_ref=ref, ods_source=source)


def test_retained_preflight_refuses_unverified_selected_manifest(installed):
    root, preparation, digest, services, ref, checks, source = installed
    access = retain.helper('pixel-macos-access-install.py')
    access._bundle.expected_release_selection = lambda *_args, **_kwargs: {
        'serviceBundleDigest': '0' * 64, 'pixelSourceRevision': ref,
        'serviceManifest': {'files': {}}}
    with pytest.raises(ValueError, match='native-service-manifest-changed'):
        retain.verify(root, expected_ref=ref, ods_source=source)


@pytest.mark.parametrize('changed', ['service', 'catalog'])
def test_cli_update_signal_only_for_proved_source_drift(installed, monkeypatch, changed):
    root, _, _, _, ref, _, source = installed
    if changed == 'service':
        (source / 'extensions/services/pixel-agent/host/extension_manager.py').write_bytes(b'new payload\n')
    else:
        retain.helper('pixel-native-config.py').service_catalog = lambda _source: b'new catalog\n'
    monkeypatch.setattr('sys.argv', ['pixel-native-retain.py', '--install-dir', str(root),
        '--ods-source', str(source), '--expected-ref', ref, '--allow-update'])
    assert retain.main() == 2
    monkeypatch.setattr('sys.argv', ['pixel-native-retain.py', '--install-dir', str(root),
        '--ods-source', str(source), '--expected-ref', ref])
    assert retain.main() == 1


def test_cli_update_signal_refuses_pending_protected_journal(installed, monkeypatch):
    root, _, _, _, ref, _, source = installed
    (source / 'extensions/services/pixel-agent/host/extension_manager.py').write_bytes(b'new payload\n')
    monkeypatch.setattr(retain, 'require_protected_clear',
        lambda **_kwargs: (_ for _ in ()).throw(ValueError('native-protected-transition-pending')))
    monkeypatch.setattr('sys.argv', ['pixel-native-retain.py', '--install-dir', str(root),
        '--ods-source', str(source), '--expected-ref', ref, '--allow-update'])
    assert retain.main() == 1


def test_cli_update_signal_for_proved_pixel_ref_change(installed, monkeypatch):
    root, _, _, _, ref, _, source = installed
    candidate_ref = 'c' * 40
    monkeypatch.setattr('sys.argv', ['pixel-native-retain.py', '--install-dir', str(root),
        '--ods-source', str(source), '--expected-ref', candidate_ref, '--allow-update'])
    assert retain.main() == 2
    monkeypatch.setattr('sys.argv', ['pixel-native-retain.py', '--install-dir', str(root),
        '--ods-source', str(source), '--expected-ref', ref, '--allow-update'])
    assert retain.main() == 0


def test_cli_requests_link_repair_only_for_legacy_mode_failure(installed, monkeypatch):
    root, _, _, _, ref, _, source = installed
    runtime = retain.helper('pixel-macos-access-install.py')._bundle
    class BundleError(ValueError):
        pass
    runtime.BundleError = BundleError
    runtime.verify = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        BundleError('bundle-link-unreadable'))
    argv = ['pixel-native-retain.py', '--install-dir', str(root),
            '--ods-source', str(source), '--expected-ref', ref, '--allow-update']
    monkeypatch.setattr('sys.argv', argv + ['--allow-link-repair'])
    assert retain.main() == 3
    monkeypatch.setattr('sys.argv', argv)
    assert retain.main() == 1
    runtime.verify = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        BundleError('bundle-content-changed'))
    monkeypatch.setattr('sys.argv', argv + ['--allow-link-repair'])
    assert retain.main() == 1


def test_retained_update_uses_protected_manifest_not_old_owner_preparation(installed):
    root, preparation, digest, services, ref, checks, source = installed
    (preparation / 'services/services.json').write_bytes(b'old owner preparation\n')
    assert retain.verify(root, expected_ref=ref, ods_source=source)['serviceDigest'] == services


@pytest.mark.parametrize('name', retain.PENDING_JOURNALS)
def test_protected_preflight_rejects_each_pending_journal(tmp_path, monkeypatch, name):
    state = tmp_path / 'protected'
    state.mkdir()
    (state / name).write_text('pending')
    class ProtectedFixture:
        def lstat(self):
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)

        def __truediv__(self, child):
            return state / child
    monkeypatch.setattr(retain, 'PROTECTED_STATE', ProtectedFixture())
    monkeypatch.setattr(retain.sys, 'platform', 'darwin')
    monkeypatch.setattr(retain.os, 'geteuid', lambda: 0)
    with pytest.raises(ValueError, match='native-protected-transition-pending'):
        retain.protected_clear()


def test_retained_preflight_refuses_protected_check_denial(installed, monkeypatch):
    root, preparation, digest, services, ref, checks, source = installed
    monkeypatch.setattr(retain, 'require_protected_clear',
        lambda **_kwargs: (_ for _ in ()).throw(ValueError('native-protected-state-not-clear')))
    with pytest.raises(ValueError, match='native-protected-state-not-clear'):
        retain.verify(root, expected_ref=ref, ods_source=source)


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
             'PIXEL_SOURCE_REF': '', 'INSTALL_DIR': str(tmp_path), 'LIB_DIR': '/source/lib',
             'SOURCE_ROOT': '/source'},
        check=True)
    assert result.stdout.splitlines() == [
        '/source/lib/pixel-native-retain.py' if retained else '/source/lib/pixel-native-install.py']


@pytest.mark.parametrize('status, expected', [(0, 'false'), (2, 'true'), (1, 'failed')])
def test_shell_auto_updates_only_proved_source_drift(tmp_path, status, expected):
    script = SCRIPT.read_text()
    start = script.index('if $ENABLE_PIXEL && ! $PREFLIGHT_ONLY; then')
    stop = script.index('\nif ! $OPENCLAW_EXPLICIT; then', start)
    (tmp_path / 'data/pixel-native').mkdir(parents=True)
    body = script[start:stop].replace('/usr/bin/python3', 'python_fixture')
    shell = '''set -euo pipefail
python_fixture() { printf '%s\\n' "$1"; return ''' + str(status) + '''; }
''' + body + '''
printf 'update=%s\\n' "$_PIXEL_UPDATE_REQUIRED"
'''
    result = subprocess.run(['bash'], input=shell, capture_output=True, text=True,
        env={**os.environ, 'ENABLE_PIXEL': 'true', 'PREFLIGHT_ONLY': 'false',
             'NON_INTERACTIVE': 'true', 'DRY_RUN': 'false', 'OPENCLAW_EXPLICIT': 'true',
             'PIXEL_SOURCE_REF': '', 'INSTALL_DIR': str(tmp_path), 'LIB_DIR': '/source/lib',
             'SOURCE_ROOT': '/source'})
    if expected == 'failed':
        assert result.returncode == 1
        assert 'update=' not in result.stdout
    else:
        assert result.returncode == 0
        assert result.stdout.splitlines() == ['/source/lib/pixel-native-retain.py',
                                              'update=' + expected]


@pytest.mark.parametrize('next_status, expected_update', [(0, 'false'), (2, 'true')])
def test_shell_repairs_legacy_links_once_then_reproves_retention(
        tmp_path, next_status, expected_update):
    script = SCRIPT.read_text()
    start = script.index('if $ENABLE_PIXEL && ! $PREFLIGHT_ONLY; then')
    stop = script.index('\nif ! $OPENCLAW_EXPLICIT; then', start)
    (tmp_path / 'data/pixel-native').mkdir(parents=True)
    body = script[start:stop].replace('/usr/bin/python3', 'python_fixture').replace(
        '/usr/bin/sudo', 'sudo_fixture')
    shell = '''set -euo pipefail
calls=0
python_fixture() {
    calls=$((calls+1))
    printf 'retain-%s\n' "$calls"
    if [[ $calls -eq 1 ]]; then return 3; fi
    return ''' + str(next_status) + '''
}
sudo_fixture() { printf 'protected-repair\n'; }
ai() { :; }
''' + body + '''
printf 'update=%s\n' "$_PIXEL_UPDATE_REQUIRED"
'''
    result = subprocess.run(['bash'], input=shell, capture_output=True, text=True,
        env={**os.environ, 'ENABLE_PIXEL': 'true', 'PREFLIGHT_ONLY': 'false',
             'NON_INTERACTIVE': 'true', 'DRY_RUN': 'false', 'OPENCLAW_EXPLICIT': 'true',
             'PIXEL_SOURCE_REF': '', 'INSTALL_DIR': str(tmp_path), 'LIB_DIR': '/source/lib',
             'SOURCE_ROOT': '/source'})
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        'retain-1', 'protected-repair', 'retain-2', 'update=' + expected_update]


def test_shell_dry_run_refuses_legacy_link_repair(tmp_path):
    script = SCRIPT.read_text()
    start = script.index('if $ENABLE_PIXEL && ! $PREFLIGHT_ONLY; then')
    stop = script.index('\nif ! $OPENCLAW_EXPLICIT; then', start)
    (tmp_path / 'data/pixel-native').mkdir(parents=True)
    body = script[start:stop].replace('/usr/bin/python3', 'python_fixture').replace(
        '/usr/bin/sudo', 'sudo_fixture')
    shell = '''set -euo pipefail
python_fixture() { return 3; }
sudo_fixture() { echo 'unexpected-repair'; }
ai_err() { echo "$*" >&2; }
''' + body
    result = subprocess.run(['bash'], input=shell, capture_output=True, text=True,
        env={**os.environ, 'ENABLE_PIXEL': 'true', 'PREFLIGHT_ONLY': 'false',
             'NON_INTERACTIVE': 'true', 'DRY_RUN': 'true', 'OPENCLAW_EXPLICIT': 'true',
             'PIXEL_SOURCE_REF': '', 'INSTALL_DIR': str(tmp_path), 'LIB_DIR': '/source/lib',
             'SOURCE_ROOT': '/source'})
    assert result.returncode == 1
    assert 'unexpected-repair' not in result.stdout
    assert 'dry-run left it unchanged' in result.stderr


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


@pytest.mark.parametrize('update_status, retain_status, expected', [
    (0, 0, ['/source/lib/pixel-native-update.py', '/source/lib/pixel-native-retain.py']),
    (1, 0, ['/source/lib/pixel-native-update.py']),
    (0, 1, ['/source/lib/pixel-native-update.py', '/source/lib/pixel-native-retain.py']),
])
def test_protected_update_runs_before_base_launch_and_requires_reproof(
        update_status, retain_status, expected):
    script = SCRIPT.read_text()
    start = script.index('    # Update the proved active native selection')
    stop = script.index('    # Create directory structure', start)
    body = script[start:stop].replace('/usr/bin/python3', 'python_fixture')
    shell = '''set -euo pipefail
ai() { :; }
ai_ok() { :; }
ai_err() { :; }
python_fixture() {
    printf '%s\\n' "$1"
    case "$1" in
        */pixel-native-update.py) return ''' + str(update_status) + ''' ;;
        */pixel-native-retain.py) return ''' + str(retain_status) + ''' ;;
    esac
}
ENABLE_PIXEL=true
_PIXEL_UPDATE_REQUIRED=true
NON_INTERACTIVE=true
PIXEL_SOURCE_REF=''' + 'a' * 40 + '''
INSTALL_DIR=/owner/ods
SOURCE_ROOT=/candidate/ods
LIB_DIR=/source/lib
_pixel_retain_args=(--install-dir /owner/ods)
''' + body
    result = subprocess.run(['bash'], input=shell, capture_output=True, text=True)
    assert result.stdout.splitlines() == expected
    assert result.returncode == (0 if update_status == retain_status == 0 else 1)
    assert script.index('pixel-native-update.py') < script.index('    # Create directory structure',
        script.index('# PHASE 4'))


def test_copied_native_source_is_reproved_before_base_launch():
    script = SCRIPT.read_text()
    start = script.index('    # The base source copy must match')
    stop = script.index('    # ── Bootstrap fast-start', start)
    body = script[start:stop].replace('/usr/bin/python3', 'python_fixture')
    shell = '''set -euo pipefail
python_fixture() { printf '%s\\n' "$@"; }
ai_err() { :; }
ENABLE_PIXEL=true
_PIXEL_RETAINED=true
NON_INTERACTIVE=true
PIXEL_SOURCE_REF=''' + 'a' * 40 + '''
INSTALL_DIR=/owner/ods
LIB_DIR=/source/lib
''' + body
    result = subprocess.run(['bash'], input=shell, capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.splitlines() == ['/source/lib/pixel-native-retain.py',
        '--install-dir', '/owner/ods', '--ods-source', '/owner/ods',
        '--expected-ref', 'a' * 40]
