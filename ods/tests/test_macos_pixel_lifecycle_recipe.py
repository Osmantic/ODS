"""Actual full native Compose rendering/hash controls; never calls a daemon.

Image inspection is a deterministic fixture. Only `docker compose config` and
`docker compose version` reach Docker. This proves recipe identity, not runtime.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPERS = Path(os.environ.get('ODS_LIFECYCLE_RECIPE_HELPER_ROOT', ROOT / 'installers/macos/lib'))
pytestmark = pytest.mark.skipif(os.environ.get('ODS_LIFECYCLE_COMPOSE_CONTROL') != '1',
    reason='requires the explicitly selected config-only Docker Compose lane')


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HELPERS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('literal_dollars', [False, True])
def test_full_native_recipe_keeps_ingress_hash_and_zero_scale_when_other_images_are_pinned(tmp_path, monkeypatch, literal_dollars):
    stack = load('pixel-native-lifecycle-stack')
    refresh = load('pixel-native-ingress-refresh')
    install = tmp_path / ('owner$ODS_LITERAL_OWNER' if literal_dollars else 'owner')
    install.mkdir()
    attempt = install / 'attempt'
    attempt.mkdir()
    (install / '.env').write_text('SYNTHETIC=1\n')
    files = [ROOT / name for name in ('docker-compose.base.yml', 'installers/macos/docker-compose.macos.yml',
        'extensions/services/pixel-model-relay/compose.yaml.disabled', 'extensions/services/pixel-edge/compose.yaml.disabled',
        'installers/macos/pixel-native.compose.yaml.disabled')]
    pinned_ingress = 'sha256:' + 'a' * 64
    env = {key: value for key, value in os.environ.items() if not key.startswith(('PIXEL_', 'COMPOSE_', 'DOCKER_'))}
    # A deliberately unusable daemon endpoint proves no inspect/build/pull/run
    # can accidentally reach a product engine during this control.
    env.update(DOCKER_HOST='unix:///nonexistent/native-compose-control.sock',
        PIXEL_NATIVE_UID=str(os.getuid()), PIXEL_INGRESS_GID=str(os.getgid()),
        PIXEL_NATIVE_INGRESS_IMAGE=pinned_ingress, PIXEL_NATIVE_CONFIG_SHA256='b' * 64,
        PIXEL_NATIVE_CONFIG_PATH=str(install / 'gateway$ODS_LITERAL_TOKEN.json'),
        PIXEL_NATIVE_WORKSPACE=str(install / 'workspace'), PIXEL_INGRESS_RUNTIME_DIR=str(install / 'runtime'),
        PIXEL_PREVIEW_RUNTIME_DIR=str(install / 'preview'), PIXEL_OPENWEBUI_KEY='c' * 64,
        PIXEL_MODEL_RELAY_KEY='f' * 64,
        DASHBOARD_API_KEY='d' * 64, WEBUI_SECRET='e' * 64,
        ODS_LITERAL_OWNER='MUST_NOT_EXPAND', ODS_LITERAL_TOKEN='MUST_NOT_EXPAND')
    prefix = ['docker', 'compose', '--project-name', 'ods', '--project-directory', str(install), '--env-file', '/dev/null']
    original = [*prefix, *[value for path in files for value in ('-f', str(path))]]
    calls = []
    def execute(command):
        assert command[:2] == ['docker', 'compose'] and 'config' in command
        calls.append(command)
        return subprocess.run(command, env=env, capture_output=True, text=True, check=True, timeout=30)
    document = json.loads(execute([*original, 'config', '--format', 'json']).stdout)
    assert document['services']['pixel-native-ingress']['image'] == pinned_ingress
    assert document['services']['llama-server']['deploy']['replicas'] == 0
    def hashes(command): return dict(line.split() for line in execute([*command, 'config', '--hash', '*']).stdout.splitlines())
    before = hashes(original)
    def no_operation(*args, **kwargs): pytest.fail('config-only control must never build, pull or start: ' + str(args))
    def docker(*args, **kwargs):
        assert args[:2] == ('image', 'inspect')
        image = args[-1] if args[-1].startswith('sha256:') else 'sha256:' + hashlib.sha256(args[-1].encode()).hexdigest()
        return SimpleNamespace(returncode=0, stdout=image)
    record = {'project': 'ods'}
    saved = stack.freeze(install, attempt, record, action='restart', service='',
        read_selection=lambda: (files, document), run=no_operation, docker_run=docker, checkpoint=lambda value: None)
    assert 'llama-server' not in saved['services']
    frozen = json.loads(Path(saved['path']).read_text())
    assert frozen['services']['llama-server']['deploy']['replicas'] == 0
    assert frozen['services']['pixel-native-ingress']['image'] == pinned_ingress
    replay = [*prefix, '-f', saved['path']]
    after = hashes(replay)
    assert before['pixel-native-ingress'] == after['pixel-native-ingress']
    assert before['pixel-edge'] != after['pixel-edge'], 'the control must exercise actual tag-to-ID hash drift elsewhere'
    observed = {'Image': pinned_ingress, 'Config': {'User': document['services']['pixel-native-ingress']['user'], 'Labels': {
        'com.docker.compose.project': 'ods', 'com.docker.compose.service': 'pixel-native-ingress',
        'com.docker.compose.config-hash': after['pixel-native-ingress']}}}
    monkeypatch.setattr(refresh, 'selected_ingress', lambda *args: observed)
    retry = {'project': 'ods', 'candidateHash': before['pixel-native-ingress'],
        'rollback': {'image': pinned_ingress, 'user': observed['Config']['User'], 'configHash': '0' * 64}}
    refresh.verify_current(no_operation, retry)
    observed['Config']['Labels']['com.docker.compose.config-hash'] = after['pixel-edge']
    with pytest.raises(ValueError, match='recovery-container-changed'): refresh.verify_current(no_operation, retry)
    receipt = {'literalDollars': literal_dollars, 'beforeIngressHash': before['pixel-native-ingress'],
        'frozenIngressHash': after['pixel-native-ingress'], 'beforeEdgeHash': before['pixel-edge'],
        'frozenEdgeHash': after['pixel-edge'], 'ingressImage': pinned_ingress,
        'selectedServices': saved['services'], 'zeroScaledLlamaExcluded': True, 'retryAcceptedExactIngressHash': True,
        'changedHashRefused': True, 'composeConfigCalls': len(calls), 'daemonReachable': False}
    target = os.environ.get('ODS_LIFECYCLE_COMPOSE_RECEIPTS')
    if target: (Path(target) / ('recipe-' + str(literal_dollars).lower() + '.json')).write_text(json.dumps(receipt, indent=2) + '\n')


def test_compose_rejects_conflicting_scale_and_replicas_before_selection(tmp_path):
    path = tmp_path / 'conflict.json'
    path.write_text(json.dumps({'services': {'pixel-native-ingress': {
        'image': 'sha256:' + 'a' * 64, 'scale': 1, 'deploy': {'replicas': 0}}}}))
    result = subprocess.run(['docker', 'compose', '--project-name', 'ods', '--env-file', '/dev/null',
        '-f', str(path), 'config', '--format', 'json'],
        env={**os.environ, 'DOCKER_HOST': 'unix:///nonexistent/native-compose-control.sock'},
        capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and 'scale' in result.stderr.lower() and 'replicas' in result.stderr.lower()
