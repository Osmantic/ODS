"""Persisted selection controls; no real Docker, images, or containers."""
import copy
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_lifecycle_stack',
    ROOT / 'installers/macos/lib/pixel-native-lifecycle-stack.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
IMAGE = 'sha256:' + 'a' * 64
NEW_IMAGE = 'sha256:' + 'b' * 64


def fixture(tmp_path):
    install = tmp_path / 'ods'
    install.mkdir()
    attempt = install / 'attempt'
    attempt.mkdir()
    env = install / '.env'
    env.write_text('PRIVATE=fixture\n')
    selected = install / 'stack.yml'
    selected.write_text('fixture')
    document = {'name': 'ods', 'services': {
        'pixel-native-ingress': {'image': IMAGE, 'environment': {'LITERAL': '$$HOME'}},
        'pixel-edge': {'image': 'mutable:latest', 'healthcheck': {'test': ['CMD', 'probe']}},
        'disabled': {'image': 'never-pull:latest', 'profiles': ['disabled']}}}
    record = {'project': 'ods'}
    calls = []
    tags = {'mutable:latest': IMAGE}
    fault = [None]
    missing = set()
    def run(*args, **kwargs):
        calls.append(args)
        code = int(fault[0] in ('pull', 'build'))
        record['preparationCommand'] = {'outcome': 'failed' if code else 'completed'}
        if fault[0] == 'unconfirmed':
            record['preparationCommand']['outcome'] = 'unconfirmed'
            raise subprocess.TimeoutExpired('fixture', 1)
        if not code:
            for name in args[1:]:
                if name in document['services']:
                    reference = document['services'][name]['image']
                    missing.discard(reference)
                    tags[reference] = IMAGE
        return SimpleNamespace(returncode=code, stdout='')
    def docker_run(*args, **kwargs):
        calls.append(args)
        if args[:2] == ('image', 'inspect'):
            if args[-1] in missing: return SimpleNamespace(returncode=1, stdout='')
            return SimpleNamespace(returncode=0, stdout=tags.get(args[-1], args[-1]))
        if args[0] == 'compose':
            return SimpleNamespace(returncode=0, stdout='pixel-native-ingress ' + 'c' * 64 + '\npixel-edge ' + 'd' * 64)
        name = 'pixel-edge' if any('pixel-edge' in value for value in args) else 'pixel-native-ingress'
        identity = ('e' if name == 'pixel-edge' else 'f') * 64
        if args[0] == 'ps': return SimpleNamespace(returncode=0, stdout=identity)
        if args[0] == 'inspect':
            name = 'pixel-edge' if args[1] == 'e' * 64 else 'pixel-native-ingress'
            health = 'unhealthy' if fault[0] == 'health' else 'healthy'
            return SimpleNamespace(returncode=0, stdout=json.dumps([{'Id': args[1],
                'Image': NEW_IMAGE if fault[0] == 'image' else IMAGE, 'State': {'Status': 'running', 'Health': {'Status': health}},
                'Config': {'Labels': {'com.docker.compose.project': 'ods', 'com.docker.compose.service': name,
                    'com.docker.compose.config-hash': ('0' if fault[0] == 'definition' else 'd' if name == 'pixel-edge' else 'c') * 64}}}]))
        raise AssertionError(args)
    def freeze(action='update', retry=False):
        return module.freeze(install, attempt, record, action=action, service='',
            read_selection=lambda: ([selected], copy.deepcopy(document)), run=run, docker_run=docker_run,
            checkpoint=lambda value: None, retry_preparation=retry)
    return SimpleNamespace(**locals())


def test_update_pulls_once_then_retry_reuses_exact_images_and_literal_values(tmp_path):
    f = fixture(tmp_path)
    saved = f.freeze()
    assert [call for call in f.calls if call[0] == 'pull'] == [('pull', '--ignore-buildable', 'pixel-edge')]
    f.tags['mutable:latest'] = NEW_IMAGE
    assert f.freeze() == saved
    assert len([call for call in f.calls if call[0] == 'pull']) == 1
    assert set(saved['images'].values()) == {IMAGE}
    pinned = json.loads(Path(saved['path']).read_text())
    assert pinned['services']['pixel-native-ingress']['environment']['LITERAL'] == '$$HOME'
    assert pinned['services']['pixel-edge']['image'] == IMAGE
    assert module.verify_result(saved, f.docker_run, f.install)


@pytest.mark.parametrize('change', ['env', 'cache', 'definition', 'new-service', 'file', 'frozen'])
def test_changed_inputs_or_definition_refuse_retry_without_pulling(tmp_path, change):
    f = fixture(tmp_path)
    saved = f.freeze()
    if change == 'env': f.env.write_text('CHANGED=1\n')
    if change == 'cache': (f.install / '.compose-flags').write_text('-f changed.yml')
    if change == 'definition': f.document['services']['pixel-edge']['ports'] = ['123:456']
    if change == 'new-service': f.document['services']['new'] = {'image': 'new:latest'}
    if change == 'file': f.selected.write_text('changed')
    if change == 'frozen': Path(saved['path']).write_text('{}')
    with pytest.raises(ValueError): f.freeze()
    assert len([call for call in f.calls if call[0] == 'pull']) == 1


def test_unconfirmed_pull_never_retries_new_tag_selection(tmp_path):
    f = fixture(tmp_path)
    f.fault[0] = 'pull'
    with pytest.raises(ValueError, match='preparation-failed'): f.freeze()
    f.fault[0] = None
    with pytest.raises(ValueError, match='selection-unconfirmed'): f.freeze()
    assert len([call for call in f.calls if call[0] == 'pull']) == 1


def add_build(f):
    context = f.install / 'context'
    context.mkdir()
    (context / 'Dockerfile').write_text('FROM scratch\nCOPY payload /payload\n')
    (context / 'payload').write_text('bound input')
    f.document['services']['pixel-edge']['build'] = {'context': str(context), 'dockerfile': 'Dockerfile'}
    f.missing.add('mutable:latest')
    return context


def test_missing_image_start_builds_under_same_preparation_and_pins_result(tmp_path):
    f = fixture(tmp_path)
    add_build(f)
    saved = f.freeze('start')
    assert [call for call in f.calls if call[0] in ('pull', 'build')] == [('build', 'pixel-edge')]
    assert saved['images']['pixel-edge'] == IMAGE
    assert saved['inputs']['buildInputs']['pixel-edge']['entries']['payload']['sha256']
    f.freeze('start')
    assert len([call for call in f.calls if call[0] == 'build']) == 1


def test_restart_keeps_no_build_even_when_local_image_is_missing(tmp_path):
    f = fixture(tmp_path)
    add_build(f)
    with pytest.raises(ValueError, match='local-image-required'): f.freeze('restart')
    assert not any(call[0] in ('pull', 'build') for call in f.calls)


def test_terminal_build_failure_retries_unchanged_inputs_without_extra_flag(tmp_path):
    f = fixture(tmp_path)
    add_build(f)
    f.fault[0] = 'build'
    with pytest.raises(ValueError, match='preparation-failed'): f.freeze('start')
    f.fault[0] = None
    assert f.freeze('start')['phase'] == 'selected'
    assert len([call for call in f.calls if call[0] == 'build']) == 2


def test_changed_build_input_cannot_resume_failed_build(tmp_path):
    f = fixture(tmp_path)
    context = add_build(f)
    f.fault[0] = 'build'
    with pytest.raises(ValueError): f.freeze('start')
    (context / 'payload').write_text('changed input')
    f.fault[0] = None
    with pytest.raises(ValueError, match='inputs-changed'): f.freeze('start', retry=True)
    assert len([call for call in f.calls if call[0] == 'build']) == 1


def test_unconfirmed_preparation_has_explicit_fresh_continuation(tmp_path):
    f = fixture(tmp_path)
    add_build(f)
    f.fault[0] = 'unconfirmed'
    with pytest.raises(subprocess.TimeoutExpired): f.freeze('start')
    f.fault[0] = None
    with pytest.raises(ValueError, match='selection-unconfirmed'): f.freeze('start')
    assert f.freeze('start', retry=True)['phase'] == 'selected'
    assert f.record['lifecycleStack']['explicitFreshPreparation'] is True


@pytest.mark.parametrize('fault', ['image', 'definition', 'health'])
def test_readiness_rejects_changed_actual_containers(tmp_path, fault):
    f = fixture(tmp_path)
    saved = f.freeze('restart')
    f.fault[0] = fault
    with pytest.raises(ValueError): module.verify_result(saved, f.docker_run, f.install)
    assert not any(call[0] == 'pull' for call in f.calls)


def test_wrong_resolved_project_cannot_prepare_or_activate(tmp_path):
    f = fixture(tmp_path)
    f.document['name'] = 'different'
    with pytest.raises(ValueError, match='project-selection-changed'): f.freeze()
    assert not f.calls


@pytest.mark.parametrize('scaling', [{'scale': 0}, {'deploy': {'replicas': 0}}, {'scale': 0, 'deploy': {'replicas': 0}}])
def test_zero_scaled_services_and_dependencies_are_not_image_or_runtime_targets(tmp_path, scaling):
    f = fixture(tmp_path)
    f.document['services']['llama-server'] = {'image': 'disabled:latest', **scaling}
    f.document['services']['pixel-edge']['depends_on'] = {'llama-server': {'condition': 'service_started', 'required': False}}
    saved = f.freeze('update')
    assert saved['services'] == ['pixel-edge', 'pixel-native-ingress']
    assert json.loads(Path(saved['path']).read_text())['services']['llama-server'] == f.document['services']['llama-server']
    assert module.verify_result(saved, f.docker_run, f.install)
    assert not any('disabled:latest' in call or 'llama-server' in call for call in f.calls)


def test_explicit_zero_scaled_ingress_cannot_open_a_lifecycle_transaction(tmp_path):
    f = fixture(tmp_path)
    f.document['services']['pixel-native-ingress']['deploy'] = {'replicas': 0}
    with pytest.raises(ValueError, match='ingress-selection-required'):
        module.active_services(f.document, 'pixel-native-ingress')


def test_invalid_resolved_scale_conflict_never_chooses_one_spelling(tmp_path):
    f = fixture(tmp_path)
    f.document['services']['pixel-edge'].update(scale=1, deploy={'replicas': 0})
    with pytest.raises(ValueError, match='resolved-scale-conflict'): f.freeze('restart')
    assert not f.calls


@pytest.mark.parametrize('policy', ['always', 'daily', 'weekly', 'every_24h', 'missing'])
def test_start_preserves_latest_and_refresh_policy_before_pinning(tmp_path, policy):
    f = fixture(tmp_path)
    f.document['services']['pixel-edge']['pull_policy'] = policy
    assert f.freeze('start')['phase'] == 'selected'
    assert [call for call in f.calls if call[0] == 'pull'] == [('pull', '--ignore-buildable', 'pixel-edge')]
    f.freeze('start')
    assert len([call for call in f.calls if call[0] == 'pull']) == 1


def test_root_build_binding_never_traverses_runtime_model_store(tmp_path):
    f = fixture(tmp_path)
    (f.install / '.dockerignore').write_text('*\n!app\n**/__pycache__\n')
    (f.install / 'Dockerfile').write_text('FROM scratch\nCOPY app /app\n')
    app = f.install / 'app'
    app.mkdir()
    (app / 'source').write_text('bound')
    data = f.install / 'data'
    data.mkdir()
    (data / 'model').write_text('runtime')
    f.document['services']['pixel-edge']['build'] = {'context': str(f.install)}
    saved = f.freeze('start')
    entries = saved['inputs']['buildInputs']['pixel-edge']['entries']
    assert 'app/source' in entries and not any(value.startswith('data') for value in entries)
    (data / 'model').write_text('changed runtime')
    assert f.freeze('start') == saved
    (app / 'source').write_text('changed build source')
    with pytest.raises(ValueError, match='inputs-changed'): f.freeze('start')
