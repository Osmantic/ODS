"""Exercise retained recovery through the real out-of-tree Compose resolver."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'installers/macos/lib'


def load(name):
    spec = importlib.util.spec_from_file_location(name, LIB / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


finalize = load('pixel-native-finalize')
stack = load('pixel-native-stack')


@pytest.fixture
def retained(tmp_path):
    root = tmp_path / 'ods'
    preparation = root / 'data/pixel-native/preparation'
    preparation.mkdir(parents=True, mode=0o700)
    prepared = {'status': 'prepared', 'phase': 'awaiting-protected-activation',
        'requiresActivation': True, 'home': str(root / 'data/pixel-native/home'),
        'runtimeDigest': 'a' * 64, 'serviceDigest': 'b' * 64, 'pixelSourceRef': 'c' * 40}
    activation = {'schemaVersion': 1, 'status': 'error', 'phase': 'final-health',
        'requiresRecovery': True, 'runtimeDigest': 'a' * 64, 'serviceDigest': 'b' * 64}
    for name, value in [('preparation.json', prepared), ('activation.json', activation)]:
        path = preparation / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    for relative in (*stack.installer.FRAGMENTS, 'docker-compose.base.yml', 'docker-compose.gateway-only.yml'):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('services: {}\n')
    (root / '.env').write_text('GPU_BACKEND=apple\nODS_MODE=local\nENABLE_OPEN_WEBUI=false\n')
    (root / '.env').chmod(0o600)
    (root / 'scripts').mkdir()
    (root / 'scripts/resolve-compose-stack.sh').write_text('#!/bin/bash\necho old-resolver-refused >&2\nexit 73\n')
    # Default candidate invocation still uses the installed native stack.
    for name in ('pixel-native-stack.py', 'pixel-native-install.py'):
        path = root / 'installers/macos/lib' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(LIB / name, path)
    return root


def environment():
    return {**os.environ, 'PATH': str(Path(sys.executable).parent) + ':' + os.environ['PATH']}


def snapshot(root):
    paths = [root / 'scripts/resolve-compose-stack.sh', root / '.env',
             *(root / 'data/pixel-native/preparation').glob('*.json')]
    if (root / '.compose-flags').exists():
        paths.append(root / '.compose-flags')
    return {str(p): (p.read_bytes(), p.stat().st_mode & 0o777) for p in paths}


@pytest.mark.parametrize('phase,cache,replay', [('final-health', False, False),
    ('webui-routing', False, False), ('final-health', True, False), ('final-health', True, True)])
def test_recovery_resolves_without_modifying_old_install(retained, phase, cache, replay):
    directory = retained / 'data/pixel-native/preparation'
    path = directory / 'activation.json'
    activation = json.loads(path.read_text())
    activation['phase'] = phase
    path.write_text(json.dumps(activation))
    if cache:
        (retained / '.compose-flags').write_text('-f stale.yaml\n')
    if replay:
        path = directory / 'selection-update.json'
        prepared = json.loads((directory / 'preparation.json').read_text())
        path.write_text(json.dumps(load('pixel-native-recover').selection(prepared, activation)))
        path.chmod(0o600)
    before = snapshot(retained)
    flags = finalize.compose_flags(retained, environment(), recovery=True)
    assert flags[::2] == ['-f'] * (len(flags) // 2)
    assert flags[1::2][-len(stack.installer.FRAGMENTS):] == list(stack.installer.FRAGMENTS)
    assert 'docker-compose.gateway-only.yml' in flags
    assert 'stale.yaml' not in flags
    assert snapshot(retained) == before
    assert (retained / '.compose-flags').exists() is cache


def test_default_resolvers_still_refuse_failed_selection(retained):
    with pytest.raises(subprocess.CalledProcessError) as error:
        finalize.compose_flags(retained, environment())
    assert error.value.returncode == 73
    result = subprocess.run(['/bin/bash', str(ROOT / 'scripts/resolve-compose-stack.sh'),
        '--script-dir', str(retained), '--gpu-backend', 'apple'], env=environment(),
        capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert 'Native Pixel Compose selection needs recovery' in result.stderr
    assert not result.stdout.strip()


@pytest.mark.parametrize('fault', ['digest', 'status', 'phase', 'private-mode', 'directory-mode',
    'home', 'ref', 'missing-fragment', 'missing-activation', 'changed-update', 'symlink'])
def test_recovery_resolver_refuses_uncertain_selection(retained, fault):
    directory = retained / 'data/pixel-native/preparation'
    path = directory / ('preparation.json' if fault in ('home', 'ref') else 'activation.json')
    value = json.loads(path.read_text())
    changes = {'digest': ('serviceDigest', 'd' * 64), 'status': ('status', 'activating'),
               'phase': ('phase', 'protected-activation'), 'home': ('home', '/wrong'),
               'ref': ('pixelSourceRef', 'not-an-exact-ref')}
    if fault in changes:
        key, changed = changes[fault]
        value[key] = changed
        path.write_text(json.dumps(value))
    if fault == 'private-mode': path.chmod(0o644)
    if fault == 'directory-mode': directory.chmod(0o755)
    if fault == 'missing-fragment': (retained / stack.installer.FRAGMENTS[0]).unlink()
    if fault == 'missing-activation': path.unlink()
    if fault == 'changed-update':
        update = directory / 'selection-update.json'
        update.write_text('{}')
        update.chmod(0o600)
    if fault == 'symlink':
        target = path.with_name('original.json')
        path.rename(target)
        path.symlink_to(target)
    before = snapshot(retained)
    with pytest.raises(subprocess.CalledProcessError):
        finalize.compose_flags(retained, environment(), recovery=True)
    assert snapshot(retained) == before
