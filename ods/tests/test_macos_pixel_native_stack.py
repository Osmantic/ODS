import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_stack',
    ROOT / 'installers/macos/lib/pixel-native-stack.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def installation(root):
    directory = root / 'data/pixel-native/preparation'
    directory.mkdir(parents=True)
    prepared = {'status': 'prepared', 'phase': 'awaiting-protected-activation',
        'home': str(root / 'data/pixel-native/home'), 'runtimeDigest': 'a' * 64, 'serviceDigest': 'b' * 64}
    record = {**prepared, 'status': 'ready', 'phase': 'services-ready'}
    for name, value in [('preparation.json', prepared), ('activation.json', record)]:
        path = directory / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    for fragment in module.installer.FRAGMENTS:
        path = root / fragment
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('services: {}\n')
    return directory


@pytest.mark.parametrize('fault', [None, 'malformed', 'mismatch', 'symlink'])
def test_atomic_update_selection_supersedes_original_without_mutating_it(tmp_path, fault):
    directory = installation(tmp_path)
    before = (directory / 'preparation.json').read_bytes()
    previous, activation = module.read_selection(directory)
    previous['runtimeDigest'] = activation['runtimeDigest'] = 'c' * 64
    selection = directory / module.UPDATE_SELECTION
    selection.write_text(json.dumps({'schemaVersion': 1, 'preparation': previous, 'activation': activation}))
    if fault == 'malformed': selection.write_text('{}')
    if fault == 'mismatch':
        activation['runtimeDigest'] = 'd' * 64
        selection.write_text(json.dumps({'schemaVersion': 1, 'preparation': previous, 'activation': activation}))
    if fault == 'symlink':
        selection.unlink()
        selection.symlink_to(directory / 'preparation.json')
    if fault:
        with pytest.raises((ValueError, OSError)): module.resolve_files(tmp_path, [])
    else:
        assert module.resolve_files(tmp_path, []) == list(module.installer.FRAGMENTS)
        assert module.read_selection(directory)[0]['runtimeDigest'] == 'c' * 64
    assert (directory / 'preparation.json').read_bytes() == before


@pytest.mark.parametrize('fault', [None, 'no-native', 'partial', 'digest', 'home', 'missing', 'symlink', 'broken-record', 'array'])
def test_native_selection_recovers_fixed_order_and_rejects_uncertain_state(tmp_path, fault):
    directory = installation(tmp_path)
    if fault == 'no-native': (directory / 'activation.json').unlink()
    if fault in ('partial', 'digest', 'home'):
        path = directory / ('preparation.json' if fault == 'home' else 'activation.json')
        data = json.loads(path.read_text())
        data[{'partial': 'status', 'digest': 'runtimeDigest', 'home': 'home'}[fault]] = 'wrong'
        path.write_text(json.dumps(data))
    if fault in ('symlink', 'missing'):
        path = tmp_path / module.installer.FRAGMENTS[0]
        path.unlink()
        if fault == 'symlink': path.symlink_to(directory / 'activation.json')
    if fault == 'broken-record':
        path = directory / 'activation.json'
        path.unlink()
        path.symlink_to(directory / 'absent')
    if fault == 'array': (directory / 'activation.json').write_text('[]')
    original = ['docker-compose.base.yml', module.installer.FRAGMENTS[2],
        str(tmp_path / module.installer.FRAGMENTS[0]), 'docker-compose.override.yml']
    if fault not in (None, 'no-native'):
        with pytest.raises((ValueError, OSError)): module.resolve_files(tmp_path, original)
    elif fault == 'no-native':
        assert module.resolve_files(tmp_path, original) == original
    else:
        expected = ['docker-compose.base.yml', 'docker-compose.override.yml', *module.installer.FRAGMENTS]
        assert module.resolve_files(tmp_path, original) == expected
        assert module.resolve_files(tmp_path, expected) == expected
        assert module.resolve_flags(tmp_path, '-f docker-compose.base.yml') == ' '.join(
            '-f ' + value for value in ['docker-compose.base.yml', *module.installer.FRAGMENTS])


@pytest.mark.parametrize('flags', ['--project-name other', '-f', '-f "a b.yml"', '-f *.yml'])
def test_unrepresentable_shell_flags_are_not_silently_reinterpreted(tmp_path, flags):
    with pytest.raises(ValueError): module.resolve_flags(tmp_path, flags)


@pytest.mark.parametrize('fault', [None, 'partial', 'directory', 'current-digest', 'phase',
    'storage-digest', 'storage-service', 'storage-internal'])
def test_completed_migration_supersedes_vm_routing_without_deleting_it(tmp_path, fault):
    directory = installation(tmp_path)
    prepared_path = directory / 'preparation.json'
    prepared = json.loads(prepared_path.read_text())
    prepared.pop('home')
    prepared.update(kind='legacy-native', phase='awaiting-joint-activation',
        installDir=str(tmp_path), currentDigest='c' * 64)
    storage = {'volumes': {name: {'external': True, 'name': 'old-' + name} for name in (
        'pixel-native-runtime', 'pixel-native-previews', 'pixel-native-preview-runtime', 'pixel-transition-state')}}
    if fault == 'storage-service': storage['services'] = {'other': {}}
    if fault == 'storage-internal': storage['volumes']['pixel-native-runtime']['external'] = False
    digest = hashlib.sha256(json.dumps(storage, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    (tmp_path / module.MIGRATION_STORAGE).write_text(json.dumps(storage))
    prepared['storageDigest'] = digest
    activation_path = directory / 'activation.json'
    activation = json.loads(activation_path.read_text())
    activation['storageDigest'] = digest if fault != 'storage-digest' else 'wrong'
    activation_path.write_text(json.dumps(activation))
    if fault == 'directory': prepared['installDir'] = str(tmp_path / 'other')
    if fault == 'current-digest': prepared['currentDigest'] = 'invalid'
    if fault == 'phase': prepared['phase'] = 'awaiting-protected-activation'
    prepared_path.write_text(json.dumps(prepared))
    if fault == 'partial':
        (directory / 'activation.json').write_text('{"status":"error"}')
    legacy = 'extensions/services/pixel-vm-link/compose.yaml'
    path = tmp_path / legacy
    path.parent.mkdir(parents=True)
    path.write_text('services: {}\n')
    original = ['docker-compose.base.yml', legacy, str(path), 'custom.yml']
    if fault:
        with pytest.raises(ValueError, match='needs-recovery'):
            module.resolve_files(tmp_path, original)
    else:
        expected = ['docker-compose.base.yml', 'custom.yml', *module.installer.FRAGMENTS,
            module.MIGRATION_STORAGE]
        assert module.resolve_files(tmp_path, original) == expected
        assert module.resolve_files(tmp_path, expected) == expected
    assert path.read_text() == 'services: {}\n'


@pytest.mark.parametrize('mode', ['ready', 'partial'])
def test_shared_resolver_restores_native_selection_without_cache(tmp_path, mode):
    directory = installation(tmp_path)
    if mode == 'partial':
        (directory / 'activation.json').write_text('{"status":"error"}')
    (tmp_path / 'docker-compose.base.yml').write_text('services: {}\n')
    (tmp_path / 'installers/macos/lib').mkdir(parents=True, exist_ok=True)
    for name in ('pixel-native-stack.py', 'pixel-native-install.py'):
        shutil.copyfile(ROOT / 'installers/macos/lib' / name, tmp_path / 'installers/macos/lib' / name)
    result = subprocess.run(['bash', str(ROOT / 'scripts/resolve-compose-stack.sh'),
        '--script-dir', str(tmp_path), '--gpu-backend', 'apple'], text=True, capture_output=True,
        env={**os.environ, 'PATH': str(Path(sys.executable).parent) + ':' + os.environ['PATH']})
    if mode == 'partial':
        assert result.returncode != 0 and 'Native Pixel Compose selection needs recovery' in result.stderr
        assert not result.stdout.strip()
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().endswith(' '.join('-f ' + value for value in module.installer.FRAGMENTS))
        assert not (tmp_path / '.compose-flags').exists()


@pytest.mark.parametrize('fault', ['none', 'missing-image', 'invalid-image', 'missing-service', 'config-error'])
def test_cli_update_does_not_pull_a_local_ingress_image_id(tmp_path, fault):
    installation(tmp_path)
    script = (ROOT / 'installers/macos/ods-macos.sh').read_text()
    function = script[script.index('compose_pull_with_retry() {'):script.index('\nread_ods_env() {')]
    shell = '''set -euo pipefail
read_env_value() { if [[ "$FAULT" == invalid-image ]]; then echo invalid; else echo "$IMAGE"; fi; }
ai_err() { echo "$*" >&2; }
ai_warn() { echo "$*" >&2; }
docker() {
    if [[ "$1" == image ]]; then
        [[ "$FAULT" != missing-image ]] || return 1
        echo "$IMAGE"
    elif [[ "$*" == *'config --services'* ]]; then
        [[ "$FAULT" != config-error ]] || return 1
        echo dashboard-api
        echo open-webui
        if [[ "$FAULT" != missing-service ]]; then echo pixel-native-ingress; fi
    else
        printf '%s\\n' "$*" >> "$PULL_LOG"
    fi
}
''' + function + '\ncompose_pull_with_retry "-f docker-compose.base.yml"\n'
    log = tmp_path / 'pull.log'
    result = subprocess.run(['bash'], input=shell, capture_output=True, text=True,
        env={**os.environ, 'INSTALL_DIR': str(tmp_path), 'FAULT': fault, 'IMAGE': 'sha256:' + 'a' * 64,
            'PULL_LOG': str(log)})
    if fault == 'none':
        assert result.returncode == 0, result.stderr
        assert log.read_text().strip() == 'compose -f docker-compose.base.yml pull --ignore-buildable dashboard-api open-webui'
    else:
        assert result.returncode != 0
        assert not log.exists()


@pytest.mark.parametrize('mode', ['ready', 'partial', 'broken'])
def test_cli_cached_flags_use_the_same_native_selection_rules(tmp_path, mode):
    directory = installation(tmp_path)
    if mode == 'partial':
        (directory / 'activation.json').write_text('{"status":"error"}')
    if mode == 'broken':
        (directory / 'activation.json').unlink()
        (directory / 'activation.json').symlink_to(directory / 'absent')
    (tmp_path / 'installers/macos/lib').mkdir(parents=True, exist_ok=True)
    for name in ('pixel-native-stack.py', 'pixel-native-install.py'):
        shutil.copyfile(ROOT / 'installers/macos/lib' / name, tmp_path / 'installers/macos/lib' / name)
    script = (ROOT / 'installers/macos/ods-macos.sh').read_text()
    function = script[script.index('get_compose_flags() {'):script.index('\n_get_base_compose_flags() {')]
    shell = 'set -euo pipefail\n_get_base_compose_flags() { echo "-f docker-compose.base.yml"; }\n' + function + '\nget_compose_flags\n'
    result = subprocess.run(['bash'], input=shell, text=True, capture_output=True,
        env={**os.environ, 'INSTALL_DIR': str(tmp_path)})
    if mode == 'ready':
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ' '.join('-f ' + value for value in ['docker-compose.base.yml', *module.installer.FRAGMENTS])
    else:
        assert result.returncode != 0 and not result.stdout.strip()
