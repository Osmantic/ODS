"""Owner client rollout keeps the retained recipe and admission boundary."""
import copy
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('native_client_update',
    Path(__file__).resolve().parents[1] / 'installers/macos/owner/pixel-native-client-update.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def selected_tags(observations):
    return {name: value['Config']['Image'] for name, value in observations.items()}


@pytest.fixture
def recipe(tmp_path):
    installed = tmp_path.resolve() / 'ods'
    installed.mkdir()
    document = {'name': 'ods', 'networks': {'default': {'name': 'ods_default'}},
        'volumes': {}, 'services': {}}
    observations, images = {}, {}
    for index, name in enumerate(module.SERVICES):
        image = 'sha256:' + str(index + 1) * 64
        images[name] = image
        document['services'][name] = {
            'build': {'context': str(installed / 'extensions/services' / name), 'dockerfile': 'Dockerfile'},
            'container_name': 'ods-' + name, 'restart': 'unless-stopped',
            'networks': {'default': {}}, 'ports': [{'target': 3002 + index, 'published': str(3002 + index)}],
            'environment': {'KEEP': 'retained-value'},
            'volumes': [{'type': 'bind', 'source': str(installed / 'data'), 'target': '/data', 'read_only': False}],
            'healthcheck': {'test': ['CMD', 'true']},
        }
        observations[name] = {
            'Id': str(index + 3) * 64, 'Image': image, 'Name': '/ods-' + name,
            'Config': {'Image': 'ods-' + name, 'Labels': {'com.docker.compose.project': 'ods',
                'com.docker.compose.service': name, 'com.docker.compose.config-hash': str(index + 5) * 64}},
            'Mounts': [{'Type': 'bind', 'Source': str(installed / 'data'), 'Destination': '/data', 'RW': True}],
            'NetworkSettings': {'Networks': {'ods_default': {}}},
            'State': {'Running': True, 'Status': 'running', 'Health': {'Status': 'healthy'}},
        }
    return installed, document, observations, images


def test_compatible_retains_existing_transport_and_configuration(recipe):
    installed, previous, _observations, _images = recipe
    candidate = copy.deepcopy(previous)
    assert module.compatible(previous, candidate, installed) is None
    assert candidate == previous


@pytest.mark.parametrize('change', ['mount-source', 'mount-readonly', 'environment', 'port', 'network',
    'build-arguments', 'remote-build', 'custom-dockerfile', 'unrelated-volume'])
def test_candidate_cannot_silently_replace_retained_choices(recipe, change):
    installed, previous, _observations, _images = recipe
    candidate = copy.deepcopy(previous)
    definition = candidate['services']['dashboard-api']
    if change == 'mount-source':
        definition['volumes'][0]['source'] = str(installed / 'other-data')
    elif change == 'mount-readonly':
        definition['volumes'][0]['read_only'] = True
    elif change == 'environment':
        definition['environment']['KEEP'] = 'changed'
    elif change == 'port':
        definition['ports'][0]['published'] = '9999'
    elif change == 'network':
        definition['networks']['unselected'] = {}
    elif change == 'build-arguments':
        definition['build']['args'] = {'OWNER_FEATURE': 'on'}
    elif change == 'remote-build':
        definition['build']['context'] = 'https://example.test/other.git'
    elif change == 'custom-dockerfile':
        definition['build']['dockerfile'] = 'Owner.Dockerfile'
    else:
        definition['volumes'].append({'type': 'bind', 'source': str(installed / 'private'), 'target': '/private'})
    with pytest.raises(ValueError, match='native-client-'):
        module.compatible(previous, candidate, installed)


def test_replay_keeps_normal_compose_image_selection_networks_and_mounts(recipe):
    _installed, document, observations, images = recipe
    previous = copy.deepcopy(document)
    replay = module.replay_document(document, observations, selected_tags(observations), verify_observed=True)
    assert document == previous
    assert replay['networks'] == {'default': {'name': 'ods_default', 'external': True}}
    for name in module.SERVICES:
        selected = replay['services'][name]
        assert selected.get('image') == document['services'][name].get('image')
        assert selected['build'] == document['services'][name]['build']
        assert selected['volumes'] == document['services'][name]['volumes']
        assert not {'depends_on', 'pull_policy'} & set(selected)


@pytest.mark.parametrize('change', ['mount-source', 'mount-readonly', 'mount-type', 'missing-network', 'duplicate-mount'])
def test_replay_refuses_a_recipe_that_does_not_describe_the_running_container(recipe, change):
    _installed, document, observations, images = recipe
    if change == 'mount-source':
        observations['dashboard-api']['Mounts'][0]['Source'] += '-different'
    elif change == 'mount-readonly':
        observations['dashboard-api']['Mounts'][0]['RW'] = False
    elif change == 'mount-type':
        observations['dashboard-api']['Mounts'][0]['Type'] = 'volume'
    elif change == 'missing-network':
        observations['dashboard-api']['NetworkSettings']['Networks'] = {}
    else:
        observations['dashboard-api']['Mounts'].append(copy.deepcopy(observations['dashboard-api']['Mounts'][0]))
    with pytest.raises(ValueError, match='native-client-'):
        module.replay_document(document, observations, selected_tags(observations), verify_observed=True)


@pytest.mark.parametrize('operation', ['apply', 'restore'])
def test_apply_and_restore_require_live_admission_before_any_effect(tmp_path, monkeypatch, operation):
    record = {'phase': 'applying', 'transport': {'project': 'ods'}}
    calls = []
    def forbidden(*_args, **_kwargs):
        calls.append('effect')
        raise AssertionError('effect before held admission')
    def require_held(*_args, **_kwargs):
        calls.append('held')
        raise ValueError('test-held-gate')
    monkeypatch.setattr(module, 'load', lambda _path: (record, tmp_path, forbidden))
    monkeypatch.setattr(module, 'require_held', require_held, raising=False)
    monkeypatch.setattr(module, 'host_command', forbidden)
    monkeypatch.setattr(module, 'helper', lambda _name: SimpleNamespace(verify=forbidden, restore=forbidden))
    with pytest.raises(ValueError, match='test-held-gate'):
        getattr(module, operation)(tmp_path)
    assert calls == ['held']


@pytest.mark.parametrize('fault', ['image', 'health', 'tag', 'configHash', 'mount', 'recipe-hash'])
def test_verify_rejects_wrong_selected_container_before_host_probe(recipe, tmp_path, monkeypatch, fault):
    installed, document, observations, images = recipe
    baseline = {name: module.container_identity(value) for name, value in observations.items()}
    record = {'installDir': str(installed), 'transport': {'project': 'ods'}, 'images': images,
        'previous': baseline, 'phase': 'applying', 'candidateClients': document['services']}
    # Container proof is a captured immutable recipe, not a fresh assumption
    # based on whatever container happens to be running at verification time.
    record['candidateHashes'] = {name: value['Config']['Labels']['com.docker.compose.config-hash']
        for name, value in observations.items()}
    candidate_recipe = module.replay_document(document, observations, selected_tags(observations), verify_observed=True)
    record['candidateRecipeSha256'] = module.digest(candidate_recipe)
    module.write(tmp_path / 'candidate.compose.json', candidate_recipe)
    if fault == 'image':
        observations['dashboard-api']['Image'] = 'sha256:' + 'a' * 64
    elif fault == 'health':
        observations['dashboard-api']['State']['Health']['Status'] = 'unhealthy'
    elif fault == 'configHash':
        observations['dashboard-api']['Config']['Labels']['com.docker.compose.config-hash'] = 'b' * 64
    elif fault == 'mount':
        observations['dashboard-api']['Mounts'][0]['Source'] += '-different'
    elif fault == 'recipe-hash':
        candidate_recipe['services']['dashboard-api']['environment']['KEEP'] = 'tampered'
        module.write(tmp_path / 'candidate.compose.json', candidate_recipe)
    monkeypatch.setattr(module, 'load', lambda _path: (record, installed, None))
    def host_probe(*_args, **_kwargs):
        raise AssertionError('host probe must not precede container refusal')
    monkeypatch.setattr(module, 'host_loaded', host_probe)
    monkeypatch.setattr(module, 'container', lambda _run, _project, name: observations[name])
    monkeypatch.setattr(module, 'image_id', lambda _run, tag: 'sha256:' + 'c' * 64 if fault == 'tag' else
        images[next(name for name in module.SERVICES if baseline[name]['tag'] == tag)])
    def helper(name):
        if name == 'pixel-native-client-files':
            return SimpleNamespace(verify=lambda *_args, **_kwargs: True)
        raise AssertionError('host probe must not precede container refusal')
    monkeypatch.setattr(module, 'helper', helper)
    with pytest.raises(ValueError, match='native-client-'):
        module.verify(tmp_path)


def test_verify_accepts_exact_healthy_recipe_and_authenticated_host_probe(recipe, tmp_path, monkeypatch):
    installed, document, observations, images = recipe
    previous = {name: module.container_identity(value) for name, value in observations.items()}
    candidate = module.replay_document(document, observations, selected_tags(observations), verify_observed=True)
    module.write(tmp_path / 'candidate.compose.json', candidate)
    record = {'installDir': str(installed), 'transport': {'project': 'ods'}, 'images': images,
        'previous': previous, 'candidateRecipeSha256': module.digest(candidate),
        'candidateHashes': {name: value['configHash'] for name, value in previous.items()}}
    probes = []
    monkeypatch.setattr(module, 'load', lambda _path: (record, installed, None))
    monkeypatch.setattr(module, 'container', lambda _run, _project, name: observations[name])
    monkeypatch.setattr(module, 'image_id', lambda _run, tag:
        images[next(name for name in module.SERVICES if previous[name]['tag'] == tag)])
    monkeypatch.setattr(module, 'host_loaded', lambda _record: True)
    files = SimpleNamespace(verify=lambda *_args, **_kwargs: True)
    installer = SimpleNamespace(_env_file=lambda _path: {'ODS_AGENT_KEY': 'fixture-key'})
    monkeypatch.setattr(module, 'helper', lambda name: files if name == 'pixel-native-client-files' else installer)
    monkeypatch.setattr(module, 'checked', lambda _run, *args, **kwargs: probes.append((args, kwargs)))
    assert module.verify(tmp_path)
    assert len(probes) == 2
    assert probes[0][0][:4] == ('exec', '-i', 'ods-dashboard-api', 'python3')
    assert all('fixture-key' not in argument for argument in probes[0][0])
    assert json.loads(probes[0][1]['input']) == {'host': 'host.docker.internal', 'port': '7710', 'key': 'fixture-key'}
    assert 'http://pixel-edge:9595/health' in probes[1][0][-1]


@pytest.mark.parametrize('phase', ['ready', 'prepared', 'building', 'unknown'])
def test_released_or_unprepared_journal_cannot_acquire_a_rollback_hold(tmp_path, monkeypatch, phase):
    def forbidden(_name):
        raise AssertionError('unqualified phase reached provider')
    monkeypatch.setattr(module, 'helper', forbidden)
    with pytest.raises(ValueError, match='native-client-held-operation-required'):
        module.require_held({'phase': phase}, tmp_path, None)


def test_existing_hold_is_reproved_with_the_same_binding(tmp_path, monkeypatch):
    calls = []
    binding = {'token': 'a' * 64, 'revision': 7}
    compose = SimpleNamespace(_managed_edge=lambda _run, project: 'edge-' + project,
        _edge_transition=lambda run, edge, key, **kwargs: calls.append((run, edge, key, kwargs)))
    installer = SimpleNamespace(_env_file=lambda _path: {'DASHBOARD_API_KEY': 'fixture-key'})
    monkeypatch.setattr(module, 'helper', lambda name: compose if name == 'pixel-native-compose' else installer)
    assert module.require_held({'phase': 'failed', 'binding': binding, 'transport': {'project': 'ods'}},
        tmp_path, 'runner') == 'edge-ods'
    assert calls == [('runner', 'edge-ods', 'fixture-key', {'binding': binding})]


def test_modified_rollback_recipe_is_refused_before_host_or_container_changes(tmp_path, monkeypatch):
    original = {'name': 'ods', 'services': {'dashboard-api': {'image': 'sha256:' + 'a' * 64}}}
    record = {'phase': 'failed', 'previousRecipeSha256': module.digest(original)}
    module.write(tmp_path / 'previous.compose.json', {'name': 'ods', 'services': {}})
    calls = []
    def forbidden(*_args, **_kwargs):
        calls.append('effect')
        raise AssertionError('changed recipe reached a live mutation')
    monkeypatch.setattr(module, 'load', lambda _path: (record, tmp_path, forbidden))
    monkeypatch.setattr(module, 'require_held', lambda *_args: calls.append('held'))
    monkeypatch.setattr(module, 'host_command', forbidden)
    with pytest.raises(ValueError, match='native-client-rollback-recipe-changed'):
        module.restore(tmp_path)
    assert calls == ['held']


@pytest.mark.parametrize('fault', [None, 'path', 'program', 'arguments', 'duplicate-path'])
def test_loaded_host_agent_must_match_its_recorded_definition(tmp_path, monkeypatch, fault):
    installed = tmp_path / 'ods'
    path = str(tmp_path / 'host-agent.plist')
    python = str(installed / '.venv/host-agent/bin/python')
    arguments = [python, str(installed / 'bin/ods-host-agent.py'), '--install-dir', str(installed)]
    record = {'installDir': str(installed), 'hostAgent': {'path': path, 'python': python}}
    actual_path = path + '.other' if fault == 'path' else path
    actual_program = python + '-other' if fault == 'program' else python
    if fault == 'arguments':
        arguments[1] = str(installed / 'bin/unrelated.py')
    output = '\n'.join(['gui/501/com.ods.host-agent = {', '\tpath = ' + actual_path,
        '\tprogram = ' + actual_program, '\tworking directory = ' + str(installed), '\targuments = {',
        *('\t\t' + argument for argument in arguments), '\t}', '}'])
    if fault == 'duplicate-path':
        output += '\n\tpath = ' + path
    monkeypatch.setattr(module.subprocess, 'run', lambda *_args, **_kwargs:
        SimpleNamespace(returncode=0, stdout=output, stderr=''))
    if fault:
        with pytest.raises(ValueError, match='native-client-loaded-host-agent-changed'):
            module.host_loaded(record)
    else:
        assert module.host_loaded(record)


@pytest.mark.parametrize('code,error,allowed', [(113, 'Could not find service "com.ods.host-agent"', True),
    (1, 'Operation not permitted', False), (113, 'Unknown failure', False)])
def test_host_absence_is_not_inferred_from_arbitrary_launchctl_errors(monkeypatch, code, error, allowed):
    monkeypatch.setattr(module.subprocess, 'run', lambda *_args, **_kwargs:
        SimpleNamespace(returncode=code, stdout='', stderr=error))
    if allowed:
        assert module.host_loaded({}, required=False) is False
    else:
        with pytest.raises(ValueError, match='native-client-host-agent-observation-failed'):
            module.host_loaded({}, required=False)


@pytest.mark.parametrize('fault', ['candidate-up', 'host-start', 'verify'])
def test_apply_failure_restores_files_images_and_host_under_the_same_hold(recipe, tmp_path, monkeypatch, fault):
    installed, document, observations, old_images = recipe
    previous = {name: module.container_identity(value) for name, value in observations.items()}
    images = {name: 'sha256:' + str(index + 7) * 64 for index, name in enumerate(module.SERVICES)}
    rollback = module.replay_document(document, observations, selected_tags(observations), verify_observed=True)
    binding = {'token': 'f' * 64, 'revision': 11}
    record = {'installDir': str(installed), 'phase': 'published', 'transport': {'project': 'ods'},
        'previous': previous, 'images': images, 'candidateClients': document['services'],
        'previousRecipeSha256': module.digest(rollback), 'binding': binding}
    tags = {previous[name]['tag']: old_images[name] for name in module.SERVICES}
    events, holds = [], []
    def require_held(value, _install, _run):
        holds.append(copy.deepcopy(value['binding']))
        events.append(('held', value['phase']))
    def checked(_run, *args, **_kwargs):
        if args[0] == 'tag':
            tags[args[2]] = args[1]
            events.append(('tag', args[2], args[1]))
            return ''
        if args[-3:-1] == ('config', '--hash'):
            return args[-1] + ' ' + ('a' if args[-1] == 'dashboard-api' else 'b') * 64
        raise AssertionError(args)
    def up(_run, _install, _project, path):
        events.append(('up', path.name))
        if path.name == 'candidate.compose.json' and fault == 'candidate-up':
            raise ValueError('injected-candidate-up')
    host_starts = []
    def host(_record, action):
        events.append(('host', action))
        if action == 'start':
            host_starts.append(action)
            if len(host_starts) == 1 and fault == 'host-start':
                raise ValueError('injected-host-start')
    def verify(_destination):
        events.append(('verify',))
        raise ValueError('injected-verify')
    files = SimpleNamespace(verify=lambda *_args, **_kwargs: events.append(('files', 'verify')),
        restore=lambda *_args: events.append(('files', 'restore')))
    private = SimpleNamespace(locked=lambda _path: nullcontext(tmp_path),
        Stores=lambda *_args, **_kwargs: SimpleNamespace(requires_stores=False))
    finalizer = SimpleNamespace(compose_flags=lambda *_args: ['-f', 'compose.yml'])
    monkeypatch.setattr(module, 'helper', lambda name: {'pixel-native-client-files': files,
        'dashboard-private-state': private, 'pixel-native-finalize': finalizer}[name])
    monkeypatch.setattr(module, 'load', lambda _path: (record, installed, None))
    monkeypatch.setattr(module, 'require_held', require_held)
    monkeypatch.setattr(module, 'compose_document', lambda *_args, **_kwargs: document)
    monkeypatch.setattr(module, 'container', lambda _run, _project, name: observations[name])
    monkeypatch.setattr(module, 'image_id', lambda _run, tag: tags[tag])
    monkeypatch.setattr(module, 'checked', checked)
    monkeypatch.setattr(module, '_up', up)
    monkeypatch.setattr(module, 'host_command', host)
    monkeypatch.setattr(module, 'verify', verify)
    monkeypatch.setattr(module, 'private_read', lambda _path: rollback)
    monkeypatch.setattr(module, 'write', lambda path, value: events.append(('write', path.name, value.get('phase'))))
    clock = iter(range(0, 1000, 100))
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(clock))
    with pytest.raises(ValueError, match='injected-' + fault):
        module.apply(tmp_path)
    assert record['phase'] == 'restored'
    assert record['binding'] == binding
    assert holds == [binding, binding]
    assert tags == {previous[name]['tag']: old_images[name] for name in module.SERVICES}
    assert events.count(('files', 'restore')) == 1
    assert ('up', 'previous.compose.json') in events
    assert events[-2:] == [('host', 'start'), ('write', 'journal.json', 'restored')]
    assert not any(event[:2] == ('write', 'journal.json') and event[-1] == 'ready' for event in events)


def test_no_clobber_rename_keeps_a_competing_owner_file(tmp_path):
    # The file helper is shared by publish/restore. RENAME_EXCL is a real
    # filesystem operation here, not a mocked absence check followed by rename.
    files = module.helper('pixel-native-client-files')
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.write_bytes(b'candidate')
    target.write_bytes(b'owner-created')
    with pytest.raises(OSError):
        files._rename(source, target)
    assert source.read_bytes() == b'candidate'
    assert target.read_bytes() == b'owner-created'


@pytest.mark.parametrize('ingress_phase', ['releasing', 'ready'])
def test_completed_clients_resume_only_the_same_ingress_release(tmp_path, monkeypatch, ingress_phase):
    installed = tmp_path / 'installed'
    preparation = installed / 'data/pixel-native/preparation'
    name = 'ingress-refresh-' + 'a' * 32
    (preparation / name).mkdir(parents=True)
    destination = tmp_path / 'clients'
    destination.mkdir()
    binding = {'token': 'b' * 64, 'revision': 'c' * 64}
    module.write(preparation / 'ingress-refresh-current.json', {'directory': name})
    module.write(preparation / name / 'refresh.json', {'phase': ingress_phase, 'binding': binding})
    record = {'phase': 'verified', 'binding': binding, 'transport': {'project': 'ods'}}
    observed = []
    installer = SimpleNamespace(_launchd=SimpleNamespace(GATEWAY_PLIST=tmp_path / 'gateway.plist'),
        _source_gateway=lambda *_args: ({}, {'OPENCLAW_CONFIG_PATH': str(tmp_path / 'config.json')}, None, None, None, None),
        _env_file=lambda _path: {'DASHBOARD_API_KEY': 'd' * 64})
    compose = SimpleNamespace(_managed_edge=lambda *_args: 'e' * 64,
        _edge_transition=lambda *_args, **_kwargs: pytest.fail('Do not reacquire a possibly released revision'))
    helpers = {'pixel-native-client-files': SimpleNamespace(), 'pixel-native-compose': compose,
        'pixel-macos-access-install': installer,
        'pixel-native-ingress-refresh': SimpleNamespace(native_idle=lambda _path: observed.append('idle'))}
    monkeypatch.setattr(module, 'load', lambda _path: (record, installed, None))
    monkeypatch.setattr(module, 'helper', helpers.__getitem__)
    monkeypatch.setattr(module, 'verify', lambda _path: observed.append('verified'))
    monkeypatch.setattr(module, 'host_command', lambda *_args: pytest.fail('Do not restart completed clients'))
    assert module.begin(destination) == (binding if ingress_phase == 'releasing' else None)
    assert observed == ['idle', 'verified']
    assert record['phase'] == ('verified' if ingress_phase == 'releasing' else 'ready')
