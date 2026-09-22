import importlib.util
import json
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('native_prepare',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-prepare.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('fault', [None, 'hash', 'mount', 'network', 'image', 'container'])
def test_rollback_snapshot_pins_running_images_and_distinct_default_networks(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    installed = tmp_path / 'installed'
    installed.mkdir()
    prep = tmp_path / 'prepared'
    prep.mkdir()
    ids = {'edge': 'a' * 64, 'preview': 'b' * 64}
    receipt = {'kind': 'legacy-native', 'status': 'prepared', 'phase': 'awaiting-joint-activation',
        'installDir': str(installed), 'legacyStorage': {'project': 'ods', 'containers': ids}}
    (prep / 'preparation.json').write_text(json.dumps(receipt))
    (prep / 'preparation.json').chmod(0o600)
    documents, inspections = {}, {}
    for key, service in (('edge', 'pixel-edge'), ('preview', 'pixel-workspace-preview')):
        path = installed / (key + '.json')
        path.write_text('{}')
        definition = {'image': 'mutable:tag', 'build': {'context': '.'},
            'environment': {'LITERAL': '$$HOME $${TOKEN} $$$$PATH'},
            'depends_on': {'other': {'condition': 'service_started'}}, 'pull_policy': 'always',
            'networks': {'default': {'aliases': [service]}},
            'volumes': [{'type': 'volume', 'source': key, 'target': '/data'}]}
        documents[service] = {'services': {service: definition},
            'networks': {'default': {'name': key + '-network'}},
            'volumes': {key: {'name': key + '-data'}}}
        inspections[ids[key]] = {'Id': ids[key], 'Name': '/ods-' + service,
            'Image': 'sha256:' + ids[key], 'Config': {'Labels': {
                'com.docker.compose.project': 'ods', 'com.docker.compose.service': service,
                'com.docker.compose.project.config_files': str(path),
                'com.docker.compose.config-hash': 'c' * 64}},
            'Mounts': [{'Type': 'volume', 'Name': key + '-data', 'Destination': '/data'}],
            'NetworkSettings': {'Networks': {key + '-network': {}}}}
    if fault == 'image': inspections[ids['edge']]['Image'] = 'mutable:tag'
    if fault == 'mount': inspections[ids['edge']]['Mounts'][0]['Name'] = 'other-data'
    if fault == 'network': inspections[ids['edge']]['NetworkSettings']['Networks'] = {}
    original_helper = module.helper
    compose = original_helper('compose')
    monkeypatch.setattr(compose, '_named_ingress', lambda runner, name:
        'd' * 64 if fault == 'container' else ids['edge' if name == 'ods-pixel-edge' else 'preview'])
    monkeypatch.setattr(compose, '_inspect_ingress', lambda runner, identity: inspections[identity])
    calls = []
    def docker(runner, *args):
        calls.append(args)
        assert args[0] == 'compose' and 'config' in args
        if '--hash' in args:
            return args[-1] + ' ' + ('bad' if fault == 'hash' else 'c' * 64)
        path = Path(args[args.index('-f') + 1])
        return json.dumps(documents['pixel-edge' if path.stem == 'edge' else 'pixel-workspace-preview'])
    monkeypatch.setattr(compose, '_docker', docker)
    monkeypatch.setattr(module, 'helper', lambda name: compose if name == 'compose' else original_helper(name))
    if fault:
        with pytest.raises(ValueError):
            module.stage_legacy_rollback(preparation=prep, docker='/docker', project='ods')
        assert not (prep / 'rollback.compose.json').exists()
    else:
        result = module.stage_legacy_rollback(preparation=prep, docker='/docker', project='ods')
        assert result['services'] == 2 and result['servicesChanged'] is False
        rollback = json.loads((prep / 'rollback.compose.json').read_text())
        assert len(rollback['networks']) == 2
        assert {item['name'] for item in rollback['networks'].values()} == {'edge-network', 'preview-network'}
        for service, definition in rollback['services'].items():
            assert definition['image'].startswith('sha256:')
            assert definition['environment']['LITERAL'] == '$$HOME $${TOKEN} $$$$PATH'
            assert not {'build', 'depends_on', 'pull_policy'} & set(definition)
        assert (prep / 'rollback.compose.json').stat().st_mode & 0o777 == 0o600
        assert json.loads((prep / 'preparation.json').read_text())['rollbackDigest'] == result['rollbackDigest']
        with pytest.raises(ValueError, match='unactivated'):
            module.stage_legacy_rollback(preparation=prep, docker='/docker', project='ods')


@pytest.mark.parametrize('fault', [None, 'activated', 'missing-container', 'workspace', 'config-drift'])
def test_storage_preparation_records_existing_data_without_docker_mutations(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    previous = tmp_path / 'previous.json'
    previous.write_text(json.dumps({'agents': {'list': [{'id': 'pixel', 'workspace': '/owner/workspace'}]}}))
    previous.chmod(0o600)
    receipt_path = tmp_path / 'preparation.json'
    receipt_path.write_text(json.dumps({'kind': 'legacy-native', 'status': 'prepared',
        'phase': 'awaiting-joint-activation', 'previousConfig': str(previous)}))
    receipt_path.chmod(0o600)
    if fault == 'activated': (tmp_path / 'activation.json').write_text('{}')
    def volume(destination, name, rw=True):
        return {'Type': 'volume', 'Destination': destination, 'Name': name, 'RW': rw}
    names = ['ods-pixel-native-ingress', 'ods-pixel-workspace-preview', 'ods-pixel-edge']
    mounts = [[volume('/runtime', 'history')], [volume('/previews', 'sites'),
        volume('/run/ods-pixel-preview', 'preview'), {'Type': 'bind', 'Destination': '/workspace',
            'Source': '/wrong' if fault == 'workspace' else '/owner/workspace', 'RW': False}],
        [volume('/pixel-runtime', 'history', False), volume('/pixel-preview-runtime', 'preview', False),
            volume('/pixel-transition-state', 'transitions')]]
    documents = {str(index + 1) * 64: {'Id': str(index + 1) * 64, 'Name': '/' + name,
        'Mounts': mounts[index], 'Config': {'Labels': {} if index == 0 else {
            'com.docker.compose.project': 'ods', 'com.docker.compose.service': name.removeprefix('ods-')}}}
        for index, name in enumerate(names)}
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[1] == 'ps':
            name = args[args.index('--filter') + 1][len('name=^/'):-1]
            output = '' if fault == 'missing-container' else str(names.index(name) + 1) * 64
        else:
            assert args[1] == 'inspect'
            output = json.dumps([documents[args[2]]])
        return SimpleNamespace(returncode=0, stdout=output)
    monkeypatch.setattr(module.subprocess, 'run', run)
    original_helper = module.helper
    compose = original_helper('compose')
    original_override = compose.legacy_storage_override
    def override(**kwargs):
        value = original_override(**kwargs)
        if fault == 'config-drift': previous.write_text('{}')
        return value
    monkeypatch.setattr(compose, 'legacy_storage_override', override)
    monkeypatch.setattr(module, 'helper', lambda name: compose if name == 'compose' else original_helper(name))
    if fault:
        with pytest.raises(ValueError):
            module.stage_legacy_storage(preparation=tmp_path, docker='/docker', project='ods')
        assert not (tmp_path / 'storage.compose.json').exists()
        assert 'storageDigest' not in json.loads(receipt_path.read_text())
    else:
        result = module.stage_legacy_storage(preparation=tmp_path, docker='/docker', project='ods')
        assert result['volumes'] == 4 and result['servicesChanged'] is False
        record = json.loads(receipt_path.read_text())
        assert record['storageDigest'] == result['storageDigest']
        assert record['legacyStorage']['containers']['ingress'] == '1' * 64
        assert (tmp_path / 'storage.compose.json').stat().st_mode & 0o777 == 0o600
        with pytest.raises(ValueError, match='unactivated'):
            module.stage_legacy_storage(preparation=tmp_path, docker='/docker', project='ods')
    assert all(call[1] in ('ps', 'inspect') for call in calls)


@pytest.mark.parametrize('fault', [None, 'configuration', 'services', 'runtime', 'layout', 'existing-home',
    'acquire', 'runtime-acquisition', 'sandbox-qualification', 'npm',
    'source', 'source-acquisition', 'auto', 'credentials', 'onboarding'])
def test_initial_preparation_orders_stages_and_records_failures(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    calls = []
    automatic = fault in ('auto', 'credentials', 'onboarding')
    acquiring = fault in ('acquire', 'runtime-acquisition', 'sandbox-qualification', 'npm')
    home, destination = tmp_path / 'Owner Home', tmp_path / 'Preparation'
    if fault == 'existing-home': home.mkdir()
    def stage(name, **kwargs):
        calls.append(name)
        assert not home.exists()
        if fault == name: raise ValueError('private credential must not enter receipt')
        if name == 'layout':
            assert kwargs['home'] == home
            home.mkdir()
            return home / 'template.plist'
        if name == 'source-acquisition': return kwargs['destination']
        if name == 'sandbox-qualification': return {'imageId': 'sha256:' + 'b' * 64}
        return kwargs['destination'] if name in ('configuration', 'onboarding') else name + '-digest'
    config = SimpleNamespace(private_answers=lambda path: {'openclawHome': str(home / '.openclaw')},
        prepare=lambda **kw: stage('configuration', **kw),
        stage_services=lambda **kw: stage('services', **kw),
        stage_bundle=lambda **kw: stage('runtime', **kw))
    config.bootstrap = SimpleNamespace(stage=lambda **kw: stage('runtime-acquisition', **kw),
        acquire_source=lambda **kw: stage('source-acquisition', **kw),
        prepare_sandbox=lambda **kw: stage('sandbox-qualification', **kw))
    monkeypatch.setattr(module, 'helper', lambda name: config if name == 'config' else
        SimpleNamespace(prepare=lambda **kw: stage('layout', **kw), write=lambda **kw: stage('onboarding', **kw),
            ensure_credentials=lambda path, **kw: stage('credentials', **kw) and b'fixture-environment',
            snapshot=lambda path: (b'fixture-environment', None), restore=lambda *args, **kw: True))
    def run():
        return module.prepare(source=None if fault in ('source', 'source-acquisition') else tmp_path,
            ref='a' * 40, answers=None if automatic else tmp_path / 'answers',
            install_dir=tmp_path if automatic else None, native_home=home if automatic else None,
            node='/node', runtime=None if acquiring else '/runtime',
            sandbox_image=None if acquiring else 'sha256:' + 'b' * 64,
            npm=None if fault == 'npm' else '/npm', license_authorized=False,
            destination=destination, docker='/docker', docker_socket='/socket', ods_source=tmp_path,
            ingress_image='sha256:' + 'c' * 64, compose_project='ods', ingress_gid=20)
    failed = fault not in (None, 'acquire', 'source', 'auto')
    if failed:
        with pytest.raises(ValueError): run()
        if fault in ('existing-home', 'npm'):
            assert not calls and not destination.exists()
            return
    else: assert run() == destination / 'preparation.json'
    receipt = json.loads((destination / 'preparation.json').read_text())
    assert receipt['requiresActivation'] is True
    assert receipt['status'] == ('error' if failed else 'prepared')
    assert receipt['phase'] == (fault if failed else 'awaiting-protected-activation')
    assert 'private credential' not in json.dumps(receipt)
    assert (destination / 'preparation.json').stat().st_mode & 0o777 == 0o600
    expected = (['source-acquisition'] if fault in ('source', 'source-acquisition') else []) + (['credentials', 'onboarding'] if automatic else []) + (['runtime-acquisition', 'sandbox-qualification'] if acquiring else []) + ['configuration', 'services', 'runtime', 'layout']
    assert calls == expected[:len(calls)]


@pytest.mark.parametrize('owner_edit', [None, 'onboarding', 'after-publication'])
def test_failed_initial_preparation_restores_credentials_unless_owner_edited(tmp_path, monkeypatch, owner_edit):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    environment = module.helper('env')
    path = tmp_path / '.env'
    before = b'LLM_MODEL=keep-model\n'
    path.write_bytes(before)
    path.chmod(0o600)
    if owner_edit == 'after-publication':
        replace = environment.os.replace
        def replace_then_edit(source, target):
            replace(source, target)
            if Path(target) == path:
                with path.open('ab') as stream: stream.write(b'OWNER_EDIT=yes\n')
        monkeypatch.setattr(environment.os, 'replace', replace_then_edit)
    def onboarding(**kwargs):
        if owner_edit == 'onboarding':
            with path.open('ab') as stream: stream.write(b'OWNER_EDIT=yes\n')
        raise ValueError('onboarding-failed')
    config = SimpleNamespace()
    monkeypatch.setattr(module, 'helper', lambda name: environment if name == 'env' else
        config if name == 'config' else SimpleNamespace(write=onboarding))
    destination = tmp_path / 'preparation'
    with pytest.raises(ValueError, match='onboarding-failed'):
        module.prepare(source=tmp_path, ref='a' * 40, node='/node', runtime='/runtime',
            sandbox_image='sha256:' + 'b' * 64, destination=destination, docker='/docker',
            docker_socket='/socket', ods_source=tmp_path, ingress_image='sha256:' + 'c' * 64,
            compose_project='ods', ingress_gid=20, install_dir=tmp_path, native_home=tmp_path / 'home')
    receipt = json.loads((destination / 'preparation.json').read_text())
    assert receipt['phase'] == 'onboarding' and receipt['status'] == 'error'
    assert receipt['credentialRecovery'] == ('review-required' if owner_edit else 'restored')
    assert (destination / 'environment-before-credentials.env').read_bytes() == before
    if owner_edit:
        assert path.read_bytes().endswith(b'OWNER_EDIT=yes\n')
    else:
        assert path.read_bytes() == before


@pytest.mark.parametrize('fault', [None, 'existing', 'onboarding', 'configuration',
    'services', 'runtime', 'joint-plan', 'source-drift'])
def test_legacy_preparation_preserves_active_files_and_keeps_phase_receipts(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    owner = SimpleNamespace(pw_uid=501, pw_name='owner')
    monkeypatch.setattr(module.pwd, 'getpwuid', lambda uid: owner)
    digest = 'a' * 64
    runtimes, configs = tmp_path / 'runtimes', tmp_path / 'configs'
    previous = configs / '501' / ('openclaw-' + digest + '.json')
    previous.parent.mkdir(parents=True)
    body = json.dumps({'agents': {'list': [{'id': 'pixel', 'workspace': str(tmp_path / 'workspace')}]}}).encode()
    previous.write_bytes(body)
    environment = tmp_path / '.env'
    environment.write_bytes(b'owner-environment')
    definition = tmp_path / 'gateway.plist'
    document = {'UserName': 'owner'}
    definition.write_bytes(plistlib.dumps(document))
    destination = tmp_path / 'migration'
    if fault == 'existing': destination.mkdir()
    env = {'OPENCLAW_CONFIG_PATH': str(previous), 'OPENCLAW_STATE_DIR': str(tmp_path / 'state'), 'HOME': str(tmp_path / 'home')}
    events = []
    def stage(name, **kwargs):
        events.append(name)
        if fault == name: raise ValueError('private failure text')
        if name == 'joint-plan':
            if fault == 'source-drift': previous.write_bytes(body + b' ')
            return {'source_bytes': definition.read_bytes()}
        if name == 'sandbox': return {'imageId': 'sha256:' + 'b' * 64}
        return kwargs.get('destination', name + '-digest') if name not in ('runtime', 'services') else 'b' * 64
    installer = SimpleNamespace(_launchd=SimpleNamespace(GATEWAY_PLIST=definition),
        _source_runtime_config=module.helper('access-install')._source_runtime_config,
        _bundle=SimpleNamespace(INSTALL_ROOT=runtimes, verify=lambda *a, **kw: None),
        RUNTIME_CONFIG_ROOT=configs, GATEWAY_LAUNCHER=tmp_path / 'launcher',
        _source_gateway=lambda *args: (document, env, None, None, runtimes / digest / 'node', runtimes / digest / 'runtime/openclaw.mjs'),
        make_migration_plan=lambda **kw: stage('joint-plan', **kw))
    config = SimpleNamespace(bootstrap=SimpleNamespace(prepare_sandbox=lambda **kw: stage('sandbox', **kw)),
        prepare=lambda **kw: stage('configuration', **kw), stage_services=lambda **kw: stage('services', **kw),
        stage_bundle=lambda **kw: stage('runtime', **kw))
    helpers = {'config': config, 'access-install': installer,
        'env': SimpleNamespace(snapshot=lambda path: (Path(path).read_bytes(), 'identity')),
        'onboarding': SimpleNamespace(write=lambda **kw: stage('onboarding', **kw))}
    monkeypatch.setattr(module, 'helper', helpers.__getitem__)
    def run():
        return module.prepare_migration(source=tmp_path, ref='c' * 40, node='/node', runtime='/runtime',
            docker='/docker', ods_source=tmp_path, install_dir=tmp_path, destination=destination,
            license_authorized=False)
    if fault:
        with pytest.raises(ValueError): run()
    else:
        assert run() == destination / 'preparation.json'
    assert environment.read_bytes() == b'owner-environment'
    assert previous.read_bytes() == body + (b' ' if fault == 'source-drift' else b'')
    if fault == 'existing':
        assert not events and not (destination / 'preparation.json').exists()
    else:
        record = json.loads((destination / 'preparation.json').read_text())
        assert record['kind'] == 'legacy-native'
        assert record['status'] == ('error' if fault else 'prepared')
        assert record['phase'] == ('joint-plan' if fault == 'source-drift' else fault or 'awaiting-joint-activation')
        assert 'private failure text' not in json.dumps(record)
        assert (destination / 'preparation.json').stat().st_mode & 0o777 == 0o600
