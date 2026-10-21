"""Actual refresh/rollback control flow with isolated Docker boundary responses."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from native_config_volume_fixture import StorageEngine, mapping

SPEC = importlib.util.spec_from_file_location('ingress_refresh',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-ingress-refresh.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
pytestmark = pytest.mark.skipif(not hasattr(os, 'getuid') or os.getuid() == 0,
    reason='requires an unprivileged POSIX owner')


def fixture(tmp_path):
    source = tmp_path / 'config.json'
    source.write_text('{"gateway":{"port":18789,"auth":{"token":"synthetic-token"}}}')
    source.chmod(0o600)
    (tmp_path / '.env').write_text('MODEL=preserve\n')
    (tmp_path / '.env').chmod(0o600)
    directory = tmp_path / 'attempt'
    directory.mkdir(mode=0o700)
    image, user = 'sha256:' + 'a' * 64, f'{os.getuid()}:{os.getgid()}'
    definition = {'image': image, 'user': user, 'environment': {'PIXEL_GATEWAY_TOKEN_FILE': '/run/pixel-config/unprovisioned.json'},
        'volumes': [{'type': 'volume', 'source': 'pixel-native-config', 'target': '/run/pixel-config', 'read_only': True}],
        'networks': {'default': {}}}
    writer = {'image': image, 'user': user, 'network_mode': 'none', 'read_only': True,
        'cap_drop': ['ALL'], 'entrypoint': ['node', '/source/native_ingress_config.mjs'],
        'labels': {'org.osmantic.ods.native-config-source': str(source)}}
    document = {'name': 'ods', 'services': {module.SERVICE: definition, 'pixel-native-config-write': writer},
        'networks': {'default': {'name': 'ods_default'}}}
    mapping(document)
    observed = {'Id': 'b' * 64, 'Image': image, 'Name': '/ods-pixel-native-ingress',
        'Config': {'User': user, 'Env': ['PIXEL_GATEWAY_TOKEN_FILE=/run/gateway.json'],
            'Labels': {'com.docker.compose.project': 'ods', 'com.docker.compose.service': module.SERVICE,
                'com.docker.compose.config-hash': 'c' * 64}},
        'Mounts': [{'Destination': '/run/gateway.json', 'Type': 'bind', 'Source': str(source), 'RW': False}],
        'NetworkSettings': {'Networks': {'ods_default': {}}}, 'State': {'Running': True}}
    return source, directory, document, observed


@pytest.mark.parametrize('fault', [None, 'hash', 'mount', 'image', 'owner', 'foreign-project', 'changed'])
def test_previous_recipe_requires_actual_compose_hash_and_mount_identity(tmp_path, fault):
    source, directory, document, observed = fixture(tmp_path)
    current = copy.deepcopy(observed)
    if fault == 'mount': observed['Mounts'][0]['RW'] = True
    if fault == 'image': observed['Image'] = 'sha256:' + 'd' * 64
    if fault == 'owner': observed['Config']['User'] = '0:0'
    if fault == 'foreign-project': observed['Config']['Labels']['com.docker.compose.project'] = 'other'
    if fault == 'changed': current['Config']['User'] = '0:0'
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == 'inspect': return SimpleNamespace(returncode=0, stdout=json.dumps([current]))
        assert args[-3:] == ('config', '--hash', module.SERVICE)
        old = json.loads(Path(args[args.index('-f') + 1]).read_text())['services'][module.SERVICE]
        assert old['environment']['PIXEL_GATEWAY_TOKEN_FILE'] == '/run/gateway.json'
        assert old['volumes'] == [{'type': 'bind', 'source': str(source), 'target': '/run/gateway.json',
            'read_only': True, 'bind': {'create_host_path': False}}]
        return SimpleNamespace(returncode=0, stdout=module.SERVICE + ' ' + ('d' if fault == 'hash' else 'c') * 64)
    def action(): return module.snapshot_previous(None, docker, directory, install_dir=tmp_path,
        project='ods', document=document, observed=observed, source=source)
    if fault:
        with pytest.raises(ValueError): action()
        assert not (directory / 'rollback.compose.json').exists()
    else:
        receipt = action()
        raw = Path(receipt['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == receipt['sha256']
        saved = json.loads(raw)
        assert saved['networks']['default'] == {'external': True, 'name': 'ods_default'}
        assert 'pixel-native-config-write' not in saved['services']
    assert all('up' not in call for call in calls)


@pytest.mark.parametrize('fault', [None, 'health-only', 'order-and-health', 'rw', 'rw-type', 'source', 'destination',
    'type', 'volume-name', 'mode', 'propagation', 'driver', 'added-field', 'removed-field',
    'missing-mount', 'extra-mount', 'duplicate-mount', 'id', 'image', 'name', 'user', 'env', 'label'])
def test_previous_recipe_tolerates_only_mount_order_and_health_changes(tmp_path, fault):
    source, directory, document, observed = fixture(tmp_path)
    document['services'][module.SERVICE]['volumes'].append({
        'type': 'volume', 'source': 'state', 'target': '/data/runtime', 'read_only': False})
    document['volumes']['state'] = {'name': 'ods_ingress_state'}
    observed['Mounts'][0].update(Mode='ro', Propagation='rprivate')
    observed['Mounts'].append({'Type': 'volume', 'Name': 'ods_ingress_state',
        'Source': '/var/lib/docker/volumes/ods_ingress_state/_data', 'Destination': '/data/runtime',
        'Driver': 'local', 'Mode': 'rw', 'RW': True, 'Propagation': ''})
    observed['State']['Health'] = {'Status': 'healthy', 'Log': [{'End': 'before'}]}
    current = copy.deepcopy(observed)
    # Both fresh Docker inspections report the same complete entries in reverse
    # order, reproducing the live failure without changing the actual mounts.
    if fault != 'health-only':
        current['Mounts'].reverse()
    volume = current['Mounts'][0]
    changes = {'rw': ('RW', False), 'rw-type': ('RW', 1), 'source': ('Source', '/foreign'),
        'destination': ('Destination', '/foreign'), 'type': ('Type', 'bind'),
        'volume-name': ('Name', 'foreign'), 'mode': ('Mode', 'ro'),
        'propagation': ('Propagation', 'rshared'), 'driver': ('Driver', 'foreign'),
        'added-field': ('UnrecognizedMountOption', True)}
    if fault in changes:
        key, value = changes[fault]
        volume[key] = value
    elif fault == 'removed-field':
        del volume['Driver']
    elif fault == 'missing-mount':
        current['Mounts'].pop()
    elif fault == 'extra-mount':
        current['Mounts'].append({'Type': 'bind', 'Source': '/foreign', 'Destination': '/foreign', 'RW': False})
    elif fault == 'duplicate-mount':
        current['Mounts'].append(copy.deepcopy(volume))
    elif fault == 'id':
        current['Id'] = 'd' * 64
    elif fault == 'image':
        current['Image'] = 'sha256:' + 'd' * 64
    elif fault == 'name':
        current['Name'] = '/foreign'
    elif fault == 'user':
        current['Config']['User'] = '0:0'
    elif fault == 'env':
        current['Config']['Env'].append('UNEXPECTED=value')
    elif fault == 'label':
        current['Config']['Labels']['com.docker.compose.config-hash'] = 'd' * 64
    elif fault in ('health-only', 'order-and-health'):
        current['State']['Health']['Log'][0]['End'] = 'later'
    before = copy.deepcopy(observed)
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[0] == 'inspect':
            assert args[1] == observed['Id']
            return SimpleNamespace(returncode=0, stdout=json.dumps([current]))
        assert args[-3:] == ('config', '--hash', module.SERVICE)
        return SimpleNamespace(returncode=0, stdout=module.SERVICE + ' ' + 'c' * 64)

    def action():
        return module.snapshot_previous(None, docker, directory, install_dir=tmp_path,
            project='ods', document=document, observed=observed, source=source)

    if fault not in (None, 'health-only', 'order-and-health'):
        error = 'native-ingress-container-changed' if fault == 'id' else 'native-ingress-previous-container-changed'
        with pytest.raises(ValueError, match=error):
            action()
        assert not (directory / 'rollback.compose.json').exists()
    else:
        receipt = action()
        raw = Path(receipt['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == receipt['sha256']
        restored = json.loads(raw)
        assert restored['volumes'] == {'state': {'name': 'ods_ingress_state', 'external': True}}
        assert restored['services'][module.SERVICE]['volumes'] == [
            {'type': 'volume', 'source': 'state', 'target': '/data/runtime', 'read_only': False},
            {'type': 'bind', 'source': str(source), 'target': '/run/gateway.json',
             'read_only': True, 'bind': {'create_host_path': False}}]
        assert receipt['previousId'] == observed['Id']
    assert observed == before, 'Comparison must preserve the original inspection'
    assert sum(call[0] == 'inspect' for call in calls) == (1 if fault == 'id' else 2)
    assert all(call[0] in ('inspect', 'compose') and 'up' not in call for call in calls)


@pytest.mark.parametrize('fault', [None, 'active', 'busy-edge', 'staging', 'recreate', 'restore', 'release', 'owner-edit'])
def test_refresh_holds_admission_stages_then_recreates_and_rolls_back_exact_env(tmp_path, monkeypatch, fault):
    source, directory, document, observed = fixture(tmp_path)
    storage = StorageEngine(tmp_path)
    compose = module.helper('pixel-native-compose')
    events = []
    monkeypatch.setattr(module, 'selected_ingress', lambda *args: observed)
    monkeypatch.setattr(compose, '_inspect_ingress', lambda *args: observed)
    monkeypatch.setattr(compose, '_managed_edge', lambda *args: 'd' * 64)
    def transition(*args, binding=None, release=False):
        events.append('release' if release else 'hold' if binding else 'inspect-edge')
        if fault == 'busy-edge' and binding is None: raise ValueError('busy')
        if fault == 'release' and release: raise ValueError('release-reply-lost')
        return {'revision': 'e' * 64}
    monkeypatch.setattr(compose, '_edge_transition', transition)
    real_helper = module.helper
    monkeypatch.setattr(module, 'helper', lambda name: compose if name == 'pixel-native-compose' else real_helper(name))
    def idle(*args):
        events.append('native-idle')
        if fault == 'active': raise ValueError('active')
    monkeypatch.setattr(module, 'native_idle', idle)
    # The snapshot routine has separate exact-hash/identity controls above.
    recipe = directory / 'rollback.compose.json'
    recipe.write_bytes(b'{}')
    recipe.chmod(0o600)
    monkeypatch.setattr(module, 'snapshot_previous', lambda *args, **kwargs: {
        'path': str(recipe), 'sha256': hashlib.sha256(b'{}').hexdigest(), 'previousId': observed['Id'],
        'image': observed['Image'], 'user': observed['Config']['User'], 'configHash': 'c' * 64})
    before = (tmp_path / '.env').read_bytes()
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    def run(*args, **kwargs):
        if args[0] == '--profile': return SimpleNamespace(returncode=0, stdout=json.dumps(document))
        if args[0] == 'config': return SimpleNamespace(returncode=0, stdout=module.SERVICE + ' ' + '9' * 64)
        if args[0] == 'run':
            events.append('init' if args[-1] == 'pixel-native-config-init' else 'stage')
            assert 'hold' in events
            return SimpleNamespace(returncode=int(fault == 'staging'), stdout=json.dumps({'sha256': expected, 'reused': False}))
        events.append('recreate')
        assert args[-1] == module.SERVICE and '--no-deps' in args
        assert expected.encode() in (tmp_path / '.env').read_bytes()
        if fault == 'owner-edit': (tmp_path / '.env').write_bytes(b'OWNER_EDIT=preserve\n')
        return SimpleNamespace(returncode=int(fault in ('recreate', 'restore', 'owner-edit')), stdout='')
    def docker(*args, **kwargs):
        if args[0] != 'compose': return storage(*args, **kwargs)
        events.append('rollback')
        assert (tmp_path / '.env').read_bytes() == before
        assert args[-1] == module.SERVICE and args[args.index('-f') + 1] == str(recipe)
        return SimpleNamespace(returncode=int(fault == 'restore'), stdout='')
    def action(): return module.refresh(run, docker, install_dir=tmp_path, directory=directory,
        project='ods', dashboard_key='f' * 64)
    if fault:
        with pytest.raises(ValueError): action()
    else:
        result = action()
        assert result['phase'] == 'ready' and result['sha256'] == expected
        assert result['environmentAfterSha256'] == hashlib.sha256((tmp_path / '.env').read_bytes()).hexdigest()
    if fault in ('active', 'busy-edge'):
        assert not any(value in events for value in ('hold', 'init', 'stage', 'recreate', 'rollback', 'release'))
    elif fault in ('staging', 'recreate'):
        assert events[-2:] == ['rollback', 'release']
        assert (tmp_path / '.env').read_bytes() == before
        # The restored old bind does not mount new storage. Its completed
        # producer receipt survives rollback for guarded uninstall instead.
        producer = json.loads((tmp_path / 'data/pixel-native/preparation/ingress-config-volume.json').read_text())
        assert producer['state'] == 'ready' and producer['fingerprint'] == {
            key: storage.volume[key] for key in ('Name', 'Labels', 'CreatedAt', 'Driver')}
    elif fault == 'restore':
        assert events[-1] == 'rollback' and 'release' not in events
    elif fault == 'owner-edit':
        assert 'rollback' not in events and 'release' not in events
        assert (tmp_path / '.env').read_bytes() == b'OWNER_EDIT=preserve\n'
    elif fault == 'release':
        assert 'rollback' not in events
        assert json.loads((directory / 'refresh.json').read_text())['phase'] == 'releasing'
    else:
        assert events.index('hold') < events.index('stage') < events.index('recreate') < events.index('release')
        assert json.loads((directory / 'refresh.json').read_text())['phase'] == 'ready'


@pytest.mark.parametrize('fault', [None, 'not-cli', 'foreign', 'paused', 'active', 'changed'])
def test_cli_may_start_only_the_exact_owned_stopped_edge_before_refresh(tmp_path, monkeypatch, fault):
    compose = module.helper('pixel-native-compose')
    before = {'Id': 'a' * 64, 'Image': 'sha256:' + 'b' * 64, 'Name': '/ods-pixel-edge', 'Mounts': [],
        'Config': {'Labels': {'com.docker.compose.project': 'foreign' if fault == 'foreign' else 'ods',
            'com.docker.compose.service': 'pixel-edge'}},
        'State': {'Running': False, 'Status': 'paused' if fault == 'paused' else 'exited'}}
    events = []
    def managed(*args):
        if 'start' not in events: raise ValueError('stopped')
        return before['Id']
    def inspected(*args):
        current = copy.deepcopy(before)
        if fault == 'changed' and 'start' in events: current['Image'] = 'sha256:' + 'c' * 64
        return current
    monkeypatch.setattr(compose, '_managed_edge', managed)
    monkeypatch.setattr(compose, '_named_ingress', lambda *args: before['Id'])
    monkeypatch.setattr(compose, '_inspect_ingress', inspected)
    monkeypatch.setattr(compose, '_edge_transition', lambda *args: events.append('readiness'))
    def docker(runner, *args):
        assert args == ('start', before['Id'])
        events.append('start')
    monkeypatch.setattr(compose, '_docker', docker)
    def idle(source):
        events.append('native-idle')
        if fault == 'active': raise ValueError('active')
    monkeypatch.setattr(module, 'native_idle', idle)
    monkeypatch.setattr(module, 'helper', lambda name: compose)
    def action(): return module.edge_ready(None, project='ods', dashboard_key='d' * 64,
        source=tmp_path / 'unused-config', allow_start=fault != 'not-cli')
    if fault:
        with pytest.raises(ValueError): action()
        if fault != 'changed': assert 'start' not in events
    else:
        assert action() == before['Id']
        assert events == ['native-idle', 'start', 'readiness']


@pytest.mark.parametrize('fault', [None, 'health-only', 'order-and-health', 'rw', 'rw-type', 'source', 'destination',
    'type', 'volume-name', 'mode', 'propagation', 'driver', 'added-field', 'removed-field',
    'missing-mount', 'extra-mount', 'duplicate-mount', 'id', 'image', 'name', 'user', 'env', 'label'])
def test_stopped_edge_tolerates_only_mount_order_and_health_changes(tmp_path, monkeypatch, fault):
    compose = module.helper('pixel-native-compose')
    before = {'Id': 'a' * 64, 'Image': 'sha256:' + 'b' * 64, 'Name': '/ods-pixel-edge',
        'Config': {'User': '501:501', 'Env': ['UNCHANGED=value'], 'Labels': {
            'com.docker.compose.project': 'ods', 'com.docker.compose.service': 'pixel-edge'}},
        'Mounts': [
            {'Type': 'volume', 'Name': 'ods_runtime', 'Source': '/volumes/ods_runtime/_data',
                'Destination': '/pixel-runtime', 'RW': False, 'Driver': 'local', 'Mode': 'ro', 'Propagation': ''},
            {'Type': 'volume', 'Name': 'ods_transition', 'Source': '/volumes/ods_transition/_data',
                'Destination': '/pixel-transition-state', 'RW': True, 'Driver': 'local', 'Mode': 'rw', 'Propagation': ''}],
        'State': {'Running': False, 'Status': 'exited'}}
    current = copy.deepcopy(before)
    current['State'] = {'Running': True, 'Status': 'running', 'Health': {'Status': 'starting'}}
    if fault != 'health-only':
        current['Mounts'].reverse()
    volume = current['Mounts'][0]
    changes = {'rw': ('RW', False), 'rw-type': ('RW', 1), 'source': ('Source', '/foreign'),
        'destination': ('Destination', '/foreign'), 'type': ('Type', 'bind'),
        'volume-name': ('Name', 'foreign'), 'mode': ('Mode', 'ro'),
        'propagation': ('Propagation', 'rshared'), 'driver': ('Driver', 'foreign'),
        'added-field': ('UnrecognizedMountOption', True)}
    if fault in changes:
        key, value = changes[fault]
        volume[key] = value
    elif fault == 'removed-field':
        del volume['Driver']
    elif fault == 'missing-mount':
        current['Mounts'].pop()
    elif fault == 'extra-mount':
        current['Mounts'].append({'Type': 'bind', 'Source': '/foreign', 'Destination': '/foreign', 'RW': False})
    elif fault == 'duplicate-mount':
        current['Mounts'].append(copy.deepcopy(volume))
    elif fault == 'id':
        current['Id'] = 'c' * 64
    elif fault == 'image':
        current['Image'] = 'sha256:' + 'c' * 64
    elif fault == 'name':
        current['Name'] = '/foreign'
    elif fault == 'user':
        current['Config']['User'] = '0:0'
    elif fault == 'env':
        current['Config']['Env'].append('UNEXPECTED=value')
    elif fault == 'label':
        current['Config']['Labels']['com.docker.compose.project'] = 'foreign'
    if fault in ('health-only', 'order-and-health'):
        current['State']['Health']['Status'] = 'healthy'
    pristine = copy.deepcopy(before)
    events = []

    def managed(*args):
        if 'start' not in events:
            raise ValueError('native-managed-edge-required')
        return before['Id']

    def docker(runner, *args):
        assert args == ('start', before['Id'])
        events.append('start')

    monkeypatch.setattr(compose, '_managed_edge', managed)
    monkeypatch.setattr(compose, '_named_ingress', lambda *args: before['Id'])
    monkeypatch.setattr(compose, '_inspect_ingress', lambda *args: current if 'start' in events else before)
    monkeypatch.setattr(compose, '_docker', docker)
    monkeypatch.setattr(compose, '_edge_transition', lambda *args: events.append('transition-ready'))
    monkeypatch.setattr(module, 'native_idle', lambda source: events.append('native-idle'))
    monkeypatch.setattr(module, 'helper', lambda name: compose)

    def action():
        return module.edge_ready(None, project='ods', dashboard_key='d' * 64,
            source=tmp_path / 'unused-config', allow_start=True)

    if fault in (None, 'health-only', 'order-and-health'):
        assert action() == before['Id']
        assert events == ['native-idle', 'start', 'transition-ready']
    else:
        with pytest.raises(ValueError, match='native-edge-definition-changed'):
            action()
        assert events == ['native-idle', 'start']
    assert before == pristine


@pytest.mark.parametrize('fault', [None, 'definition', 'source-before-stage', 'source-after-stage'])
def test_unchanged_selection_reuses_only_exact_current_config_without_restart(tmp_path, monkeypatch, fault):
    source, directory, document, observed = fixture(tmp_path)
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    selected = '/run/pixel-config/' + expected + '.json'
    document['services'][module.SERVICE]['environment']['PIXEL_GATEWAY_TOKEN_FILE'] = selected
    observed['Config']['Env'] = ['PIXEL_GATEWAY_TOKEN_FILE=' + selected]
    monkeypatch.setattr(module, 'selected_ingress', lambda *args: observed)
    called = []
    staging = module.helper('pixel-native-ingress-config')
    def stage(*args, **kwargs):
        called.append('stage')
        if fault in ('source-before-stage', 'source-after-stage'):
            source.write_text('{"gateway":{"port":19999,"auth":{"token":"changed-synthetic-token"}}}')
        return {'sha256': hashlib.sha256(source.read_bytes()).hexdigest()
            if fault == 'source-before-stage' else expected, 'reused': True}
    monkeypatch.setattr(staging, 'stage', stage)
    helper = module.helper
    monkeypatch.setattr(module, 'helper', lambda name: staging if name == 'pixel-native-ingress-config' else helper(name))
    def run(*args, **kwargs):
        if args[0] == '--profile': return SimpleNamespace(returncode=0, stdout=json.dumps(document))
        assert args == ('config', '--hash', module.SERVICE)
        return SimpleNamespace(returncode=0, stdout=module.SERVICE + ' ' + ('d' if fault == 'definition' else 'c') * 64)
    def forbidden(*args, **kwargs): pytest.fail('An unchanged selection must not start/recreate services or acquire admission')
    monkeypatch.setattr(module, 'edge_ready', forbidden)
    def action(): return module.refresh(run, forbidden, install_dir=tmp_path, directory=directory,
        project='ods', dashboard_key='f' * 64)
    if fault:
        with pytest.raises(ValueError, match='definition-changed' if fault == 'definition' else 'source-changed'): action()
    else:
        assert action()['phase'] == 'unchanged'
    assert called == ([] if fault == 'definition' else ['stage'])
    assert not (directory / 'refresh.json').exists()
    assert (tmp_path / '.env').read_text() == 'MODEL=preserve\n'


@pytest.mark.parametrize('state', [
    {'available': True, 'phase': 'idle', 'active': 0},
    {'available': True, 'phase': 'idle', 'active': 1},
    {'available': True, 'phase': 'held', 'active': 0},
    {'available': True, 'phase': 'idle', 'active': False},
    {'available': False, 'phase': 'idle', 'active': 0},
])
def test_native_idle_reads_only_selected_loopback_gateway_and_fails_closed(tmp_path, monkeypatch, state):
    source = tmp_path / 'config.json'
    source.write_text('{"gateway":{"port":19999,"auth":{"token":"synthetic-private-token"}}}')
    source.chmod(0o600)
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size):
            assert size == 65537
            return json.dumps(state).encode()
    def open_request(request, timeout):
        assert request.full_url == 'http://127.0.0.1:19999/pixel-ods/access-runtime'
        assert request.get_method() == 'GET' and request.data is None and timeout == 10
        assert request.get_header('Authorization') == 'Bearer synthetic-private-token'
        return Response()
    def opener(proxy):
        assert proxy.proxies == {}
        return SimpleNamespace(open=open_request)
    monkeypatch.setattr(module.urllib.request, 'build_opener', opener)
    if state == {'available': True, 'phase': 'idle', 'active': 0} and type(state['active']) is int:
        module.native_idle(source)
    else:
        with pytest.raises(ValueError, match='native-ingress-active'): module.native_idle(source)


@pytest.mark.parametrize('kind', ['cli-restart', 'finalize-clients', 'retained-recovery'])
@pytest.mark.parametrize('unchanged', [False, True])
@pytest.mark.parametrize('fault', [None, 'operation', 'health', 'volume', 'source', 'ingress', 'selection', 'release'])
def test_lifecycle_remains_held_through_final_work_even_for_unchanged_config(tmp_path, monkeypatch, kind, unchanged, fault):
    source, _, document, observed = fixture(tmp_path)
    storage = StorageEngine(tmp_path)
    preparation = tmp_path / 'data/pixel-native/preparation'
    preparation.mkdir(parents=True, mode=0o700, exist_ok=True)
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    if unchanged:
        selected = '/run/pixel-config/' + expected + '.json'
        document['services'][module.SERVICE]['environment']['PIXEL_GATEWAY_TOKEN_FILE'] = selected
        observed['Config']['Env'] = ['PIXEL_GATEWAY_TOKEN_FILE=' + selected]
        observed['Config']['Labels']['com.docker.compose.config-hash'] = '9' * 64
        (tmp_path / '.env').write_text('MODEL=preserve\nPIXEL_NATIVE_CONFIG_SHA256=' + expected + '\n')
    compose = module.helper('pixel-native-compose')
    real_helper = module.helper
    monkeypatch.setattr(module, 'helper', lambda name: compose if name == 'pixel-native-compose' else real_helper(name))
    events = []
    monkeypatch.setattr(module, 'selected_ingress', lambda *args: observed)
    monkeypatch.setattr(compose, '_managed_edge', lambda *args: ('b' if 'operation' in events else 'a') * 64)
    monkeypatch.setattr(module, 'native_idle', lambda *args: events.append('native-idle'))
    monkeypatch.setattr(module, 'edge_ready', lambda *args, **kwargs: 'a' * 64)
    volume = {'Name': 'ods_transition', 'Type': 'volume', 'Destination': '/pixel-transition-state', 'RW': True}
    monkeypatch.setattr(module, 'edge_storage', lambda *args: dict(volume,
        Name='foreign' if fault == 'volume' and 'operation' in events else 'ods_transition'))
    def transition(*args, binding=None, release=False):
        events.append('release' if release else 'hold' if binding else 'inspect')
        if release and fault == 'release': raise ValueError('lost-release-reply')
        return {'revision': 'e' * 64}
    monkeypatch.setattr(compose, '_edge_transition', transition)
    def health(*args, **kwargs):
        events.append('health')
        assert 'operation' in events and 'release' not in events
        if fault == 'health': raise ValueError('not-ready')
    monkeypatch.setattr(compose, 'wait_ready', health)
    def snapshot(run, docker, directory, **kwargs):
        path = directory / 'rollback.compose.json'
        path.write_text('{}')
        path.chmod(0o600)
        return {'path': str(path), 'sha256': hashlib.sha256(b'{}').hexdigest(), 'previousId': observed['Id'],
            'image': observed['Image'], 'user': observed['Config']['User'],
            'configHash': observed['Config']['Labels']['com.docker.compose.config-hash']}
    monkeypatch.setattr(module, 'snapshot_previous', snapshot)
    def run(*args, **kwargs):
        if args[0] == '--profile': return SimpleNamespace(returncode=0, stdout=json.dumps(document))
        if args[0] == 'config': return SimpleNamespace(returncode=0, stdout=module.SERVICE + ' ' + '9' * 64)
        if args[0] == 'run':
            assert 'hold' in events and 'release' not in events
            events.append('stage')
            return SimpleNamespace(returncode=0, stdout=json.dumps({'sha256': expected, 'reused': unchanged}))
        assert args[0] == 'up'
        events.append('ingress-up')
        observed['Config']['Labels']['com.docker.compose.config-hash'] = '9' * 64
        return SimpleNamespace(returncode=0, stdout='')
    def operation(selection, record, checkpoint):
        assert 'hold' in events and 'release' not in events
        assert record['phase'] == 'operating' and selection['sha256'] == expected
        events.append('operation')
        if kind == 'cli-restart': events.extend(['native-restart', 'cli-compose-up'])
        if fault == 'operation': raise ValueError('final-work-failed')
        observed['Config']['Labels']['com.docker.compose.config-hash'] = ('f' if fault == 'ingress' else '9') * 64
        if fault == 'source': source.write_text('{"owner_changed":true}')
        return 'complete'
    if fault == 'selection':
        def changed_after_readiness(record):
            assert 'health' in events
            raise ValueError('final-selection-changed')
        operation.validate_result = changed_after_readiness
    def forbidden(*args, **kwargs): pytest.fail('Final lifecycle failure must not use ingress-only rollback')
    def action(): return module.refresh_locked(run, storage, install_dir=tmp_path, project='ods',
        dashboard_key='f' * 64, operation=operation, operation_kind=kind, operation_key='8' * 64)
    if fault:
        with pytest.raises(ValueError): action()
    else:
        result = action()
        assert result['operationResult'] == 'complete'
        assert events.index('hold') < events.index('operation') < events.index('health') < events.index('release')
        assert events[-2:] == ['native-idle', 'release']
    directory = preparation / json.loads((preparation / 'ingress-refresh-current.json').read_text())['directory']
    record = json.loads((directory / 'refresh.json').read_text())
    assert record['phase'] == ('ready' if fault is None else 'releasing' if fault == 'release' else 'operation-failed')
    if fault != 'release' and fault is not None: assert 'release' not in events
    if kind == 'cli-restart':
        assert events.count('cli-compose-up') == 1 and 'ingress-up' not in events
        assert events.index('hold') < events.index('native-restart')
    if fault == 'operation':
        with pytest.raises(ValueError, match='unreleased-refresh'):
            module.rollback(run, forbidden, record, install_dir=tmp_path, dashboard_key='f' * 64, checkpoint=lambda value: None)
        with pytest.raises(ValueError, match='operation-selection-changed'):
            module.refresh_locked(run, forbidden, install_dir=tmp_path, project='ods', dashboard_key='f' * 64,
                operation=operation, operation_kind=kind, operation_key='7' * 64)
        fault = None
        assert action()['operationResult'] == 'complete'
        assert len(list(preparation.glob('ingress-refresh-*'))) == 2  # one directory plus pointer
