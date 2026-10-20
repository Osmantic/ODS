"""Real Compose hashing of the actual previous-main definition; no daemon calls."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from native_config_volume_fixture import StorageEngine

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get('ODS_INGRESS_BASELINE_ROOT')
HELPERS = Path(os.environ.get('ODS_INGRESS_RECIPE_HELPER_ROOT', ROOT / 'installers/macos/lib'))


@pytest.mark.skipif(not BASE, reason='requires the separately pinned previous-main source archive')
@pytest.mark.parametrize('literal_dollars', [False, True])
@pytest.mark.parametrize('check', ['recipe', 'label'])
def test_actual_compose_preserves_ingress_paths_values_and_rollback_hash(tmp_path, literal_dollars, check):
    baseline = Path(BASE)
    spec = importlib.util.spec_from_file_location('recipe_refresh', HELPERS / 'pixel-native-ingress-refresh.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base = tmp_path / 'base.json'
    base.write_text(json.dumps({'services': {name: {'image': 'busybox:1.36.1'} for name in ('dashboard-api', 'open-webui')}}))
    install = tmp_path / ('owner$ODS_LITERAL_OWNER' if literal_dollars else 'owner')
    install.mkdir()
    source = install / ('gateway$ODS_LITERAL_TOKEN.json' if literal_dollars else 'gateway.json')
    source.write_text('{"gateway":{"auth":{"token":"synthetic-private-token"}}}')
    source.chmod(0o600)
    runtime_value = 'literal$ODS_LITERAL_TOKEN/$${unchanged}' if literal_dollars else 'plain'
    extra = tmp_path / 'extra.json'
    extra.write_text(json.dumps({'services': {'pixel-native-ingress': {'environment': {
        'ODS_DOLLAR_CONTROL': runtime_value.replace('$', '$$')}}}}))
    env = {key: value for key, value in os.environ.items() if not key.startswith(('PIXEL_', 'COMPOSE_'))}
    env.update(PIXEL_NATIVE_UID=str(os.getuid()), PIXEL_INGRESS_GID=str(os.getgid()),
        PIXEL_NATIVE_INGRESS_IMAGE='sha256:' + 'a' * 64, PIXEL_NATIVE_CONFIG_PATH=str(source),
        PIXEL_NATIVE_WORKSPACE=str(install / 'workspace'), PIXEL_INGRESS_RUNTIME_DIR=str(install / 'runtime'),
        PIXEL_PREVIEW_RUNTIME_DIR=str(install / 'preview'), PIXEL_OPENWEBUI_KEY='b' * 64, DASHBOARD_API_KEY='c' * 64,
        ODS_LITERAL_OWNER='MUST_NOT_EXPAND', ODS_LITERAL_TOKEN='MUST_NOT_EXPAND')
    # Same install-directory resolution for old/new source; only the overlay's
    # exact source bytes change. The definitions are rendered, never started.
    def command(root):
        return ['docker', 'compose', '--profile', 'native-config-maintenance', '--project-name', 'ods',
            '--project-directory', str(install), '--env-file', '/dev/null', '-f', str(base),
            '-f', str(ROOT / 'extensions/services/pixel-edge/compose.yaml.disabled'),
            '-f', str(root / 'installers/macos/pixel-native.compose.yaml.disabled'), '-f', str(extra)]
    def execute(args): return subprocess.run(args, env=env, capture_output=True, text=True, check=True, timeout=30)
    old = json.loads(execute([*command(baseline), 'config', '--format', 'json']).stdout)
    current = json.loads(execute([*command(ROOT), 'config', '--format', 'json']).stdout)
    if check == 'label':
        # Exercise actual source-label extraction and private env selection.
        # Only container execution is substituted; rendering and files are real.
        (install / '.env').write_text('KEEP=fixture\n')
        (install / '.env').chmod(0o600)
        expected_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        def selected(*args, **kwargs):
            if args[0] == 'run':
                if args[-1] == 'pixel-native-config-init': return SimpleNamespace(returncode=0, stdout='')
                assert args[-1] == expected_digest and kwargs['input'].encode() == source.read_bytes()
                return SimpleNamespace(returncode=0, stdout=json.dumps({'sha256': expected_digest, 'reused': False}))
            return execute([*command(ROOT), *args])
        selection = module.helper('pixel-native-ingress-config').prepare(selected,
            docker_run=StorageEngine(install),
            environment_path=install / '.env', backup=install / 'before.env')
        assert selection['sha256'] == expected_digest
        assert (install / '.env').read_text() == 'KEEP=fixture\nPIXEL_NATIVE_CONFIG_SHA256=' + expected_digest + '\n'
        return
    expected = execute([*command(baseline), 'config', '--hash', module.SERVICE]).stdout.strip().split()[1]
    service = old['services'][module.SERVICE]
    observed = {'Id': 'd' * 64, 'Name': '/ods-pixel-native-ingress-1', 'Image': service['image'],
        'Config': {'User': service['user'], 'Env': [key + '=' + str(value).replace('$$', '$') for key, value in service['environment'].items()],
            'Labels': {'com.docker.compose.project': 'ods', 'com.docker.compose.service': module.SERVICE,
                'com.docker.compose.config-hash': expected}},
        'Mounts': [{'Destination': value['target'], 'Type': value['type'], 'RW': not value.get('read_only', False),
            **({'Source': value['source'].replace('$$', '$')} if value['type'] == 'bind' else {'Name': old['volumes'][value['source']]['name']})}
            for value in service['volumes']], 'NetworkSettings': {'Networks': {
                old['networks'][name]['name']: {} for name in service['networks']}}}
    def docker(*args, **kwargs):
        if args[0] == 'inspect': return SimpleNamespace(returncode=0, stdout=json.dumps([observed]))
        assert args[0] == 'compose' and args[-3:] == ('config', '--hash', module.SERVICE)
        return execute(['docker', *args])
    receipt = module.snapshot_previous(None, docker, tmp_path, install_dir=install, project='ods',
        document=current, observed=observed, source=source)
    restored = json.loads(Path(receipt['path']).read_text())
    assert restored['services'][module.SERVICE]['environment']['PIXEL_GATEWAY_TOKEN_FILE'] == '/run/gateway.json'
    assert set(restored['services']) == {module.SERVICE}
    assert receipt['configHash'] == expected
    # Compose excludes build/dependency/pull policy from the service hash.
    # The minimal restore must retain the same identity so an interrupted
    # rollback and a later refresh recognize the restored container.
    replay = ['docker', 'compose', '--project-directory', str(install),
        '--project-name', 'ods', '--env-file', '/dev/null', '-f', receipt['path'],
        'config']
    restored_hash = execute([*replay, '--hash', module.SERVICE]).stdout.strip().split()[1]
    assert restored_hash == expected
    replayed = json.loads(execute([*replay, '--format', 'json']).stdout)['services'][module.SERVICE]
    assert replayed['environment']['ODS_DOLLAR_CONTROL'].replace('$$', '$') == runtime_value
    assert next(item for item in replayed['volumes'] if item['target'] == '/run/gateway.json')['source'].replace('$$', '$') == str(source)
