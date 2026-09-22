import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('migration_installer',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-macos-access-install.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_configuration_revisions_do_not_collide_for_the_same_runtime():
    digest = 'a' * 64
    old = {'runtime_bundle': {'digest': digest, 'config_bytes': b'{"mode":"sandbox"}'},
        'migration_qualification': {'approved': True}}
    new = {'runtime_bundle': {'digest': digest, 'config_bytes': b'{"mode":"full-access"}'},
        'migration_qualification': {'approved': True}}
    first, second = module._candidate_config_names(old), module._candidate_config_names(new)
    assert first[0] != second[0]
    assert first[1] == second[1] == 'openclaw-' + digest + '.json'
    assert second[0] == 'openclaw-' + digest + '-' + hashlib.sha256(new['runtime_bundle']['config_bytes']).hexdigest() + '.json'
    assert module._candidate_config_names({'runtime_bundle': old['runtime_bundle']}) == (first[1],)
    parent = Path('/private/var/lib/ods-pixel-native-config/501')
    for name in (*first, *second, 'openclaw.json'):
        assert module._source_runtime_config(parent / name, parent, digest)
    for path in (parent.parent / second[0], parent / ('openclaw-' + 'b' * 64 + '.json'),
            parent / ('openclaw-' + digest + '-invalid.json'), parent / 'nested' / second[0], None):
        assert not module._source_runtime_config(path, parent, digest)


@pytest.mark.parametrize('fault', [None, 'active', 'state', 'candidate', 'services', 'drift'])
def test_migration_selection_binds_configuration_and_services_to_active_state(tmp_path, monkeypatch, fault):
    current_digest, candidate_digest = 'a' * 64, 'b' * 64
    current = module._bundle.INSTALL_ROOT / current_digest
    previous_path = str(module.RUNTIME_CONFIG_ROOT / '501' / ('openclaw-' + current_digest + '.json'))
    candidate, services = tmp_path / 'candidate', tmp_path / 'services'
    candidate.mkdir()
    services.mkdir()
    previous = {'agents': {'list': [{'id': 'pixel', 'workspace': '/owner/work', 'model': 'ods-gateway/ods/current'}]},
        'models': {'providers': {'ods-gateway': {'apiKey': 'fixture-only'}}},
        'gateway': {'port': 18789, 'auth': {'mode': 'token', 'token': 'fixture-only'}}}
    body = json.dumps(previous).encode()
    record = {'schemaVersion': 1, 'kind': 'legacy-native', 'stateDir': '/owner/state', 'workspace': '/owner/work',
        'agentId': 'pixel', 'requiresJointActivation': True,
        'previousConfigSha256': hashlib.sha256(json.dumps(previous, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'candidateConfigSha256': hashlib.sha256(body).hexdigest()}
    if fault == 'candidate': record['candidateConfigSha256'] = '0' * 64
    reads = []
    def read(path, uid):
        assert uid == 501
        reads.append(str(path))
        if str(path) == previous_path:
            return b'{}' if fault == 'drift' and reads.count(previous_path) > 1 else body
        if Path(path).name == 'migration.json': return json.dumps(record).encode()
        return body
    monkeypatch.setattr(module, '_configuration_bytes', read)
    monkeypatch.setattr(module.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=501))
    monkeypatch.setattr(module, '_source_gateway', lambda *args: (
        {'UserName': 'owner'}, {'OPENCLAW_CONFIG_PATH': previous_path,
            'OPENCLAW_STATE_DIR': '/other' if fault == 'state' else '/owner/state'}, None, None,
        current / ('wrong-node' if fault == 'active' else 'node'), current / 'runtime/openclaw.mjs'))
    verified = []
    monkeypatch.setattr(module._bundle, 'verify', lambda *args, **kwargs: verified.append('current'))
    monkeypatch.setattr(module, '_bundle_plan', lambda *args: {'source_config_bytes': body})
    def verify_services(path, **kwargs):
        assert kwargs['expected_config_digest'] == hashlib.sha256(body).hexdigest()
        verified.append('services')
        if fault == 'services': raise ValueError('service-bundle-mismatch')
    monkeypatch.setattr(module._native_services, 'helper', lambda name: SimpleNamespace(verified_services=verify_services))
    def run():
        return module.qualify_migration_selection(owner_name='owner', current_digest=current_digest,
            gateway_port=18789, candidate=candidate, runtime_bundle=tmp_path, bundle_digest=candidate_digest,
            services_bundle=services, services_digest='c' * 64, source_ref='d' * 40)
    if fault:
        with pytest.raises((ValueError, module.InstallError)): run()
    else:
        selected = run()
        assert selected['preservation']['stateDir'] == '/owner/state'
        assert selected['previousConfigBytes'] == body
        assert verified == ['current', 'services']
    assert not list(candidate.iterdir()) and not list(services.iterdir())


@pytest.mark.parametrize('fault', [None, 'service', 'ref', 'state', 'previous', 'candidate',
    'port', 'kind', 'missing', 'source-path'])
def test_joint_recovery_context_retains_prior_bytes_and_rejects_mixed_selections(monkeypatch, fault):
    owner = SimpleNamespace(pw_uid=501, pw_gid=20, pw_name='owner', pw_dir='/owner')
    monkeypatch.setattr(module.pwd, 'getpwnam', lambda name: owner)
    old = {'agents': {'list': [{'id': 'pixel', 'workspace': '/owner/work', 'model': 'ods-gateway/ods/current'}]},
        'models': {'providers': {'ods-gateway': {'apiKey': 'fixture'}}},
        'gateway': {'port': 18789, 'auth': {'mode': 'token', 'token': 'fixture'}}}
    body = json.dumps(old).encode()
    previous_hash = hashlib.sha256(json.dumps(old, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    candidate_hash = hashlib.sha256(body).hexdigest()
    plan = {name: {} for name in module.RECOVERY_PLAN_FIELDS}
    plan.update(owner=owner, source_bytes=b'approved-plist', key=b'fixture-key',
        source_environment={'OPENCLAW_STATE_DIR': '/owner/state'}, upgrade_kind='native-migration',
        upgrade_qualification={'currentDigest': 'a' * 64, 'candidateDigest': 'b' * 64, 'kind': 'native-migration'},
        runtime_bundle={'digest': 'b' * 64, 'source_config': '/stage/candidate/openclaw.json',
            'source_config_bytes': body, 'config_bytes': body},
        migration_source_config_bytes=body, native_manager_port=3002,
        native_services={'bundle': '/stage/services', 'expected_digest': 'c' * 64,
            'expected_ref': 'd' * 40, 'expected_config_digest': candidate_hash},
        migration_qualification={'currentDigest': 'a' * 64, 'candidateDigest': 'b' * 64,
            'serviceDigest': 'c' * 64, 'pixelSourceRef': 'd' * 40, 'candidate': '/stage/candidate',
            'transport': None,
            'preservation': {'stateDir': '/owner/state', 'workspace': '/owner/work',
                'previousConfigSha256': previous_hash, 'candidateConfigSha256': candidate_hash}})
    saved = module._recovery_context(plan)
    if fault == 'service': saved['native_services']['expected_config_digest'] = '0' * 64
    if fault == 'ref': saved['native_services']['expected_ref'] = 'e' * 40
    if fault == 'state': saved['source_environment']['OPENCLAW_STATE_DIR'] = '/other'
    if fault == 'previous': saved['migration_source_config_bytes'] = module.base64.b64encode(b'{}').decode()
    if fault == 'candidate': saved['runtime_bundle']['source_config_bytes'] = module.base64.b64encode(b'{}').decode()
    if fault == 'port': saved['native_manager_port'] = True
    if fault == 'kind': saved['upgrade_qualification']['kind'] = 'stream-progress'
    if fault == 'missing': saved.pop('native_services')
    if fault == 'source-path': saved['runtime_bundle']['source_config'] = '/other/openclaw.json'
    def run():
        return module._decode_recovery_context(saved, current_digest='a' * 64,
            candidate_digest='b' * 64, owner_name='owner')
    if fault:
        with pytest.raises(module.InstallError): run()
    else:
        recovered = run()
        assert recovered['migration_source_config_bytes'] == body
        assert recovered['native_services'] == plan['native_services']
        assert recovered['migration_qualification'] == plan['migration_qualification']


@pytest.mark.parametrize('fault', [None, 'unpublished', 'old-drift', 'mapping', 'versioned-drift'])
def test_recovery_checks_installed_paths_without_requiring_staging(monkeypatch, fault):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bin'))
    import pixel_macos_custody as custody
    current = module._bundle.INSTALL_ROOT / ('a' * 64)
    candidate = module._bundle.INSTALL_ROOT / ('b' * 64)
    parent = module.RUNTIME_CONFIG_ROOT / '501'
    old_path = str(parent / ('openclaw-' + 'a' * 64 + '.json'))
    new_path = str(parent / ('openclaw-' + 'b' * 64 + '.json'))
    source = {'plugins': {'load': {'paths': ['/staging/removed/plugin']}}}
    mapped = {'plugins': {'load': {'paths': [str(candidate / 'plugins/0')]}}}
    mapped_body = (json.dumps(mapped, indent=2, ensure_ascii=True) + '\n').encode()
    plan = {'owner': SimpleNamespace(pw_uid=501), 'migration_qualification': {'approved': True},
        'migration_source_config_bytes': b'old-config', 'source_environment': {'OPENCLAW_CONFIG_PATH': old_path},
        'upgrade_qualification': {'currentDigest': 'a' * 64, 'candidateDigest': 'b' * 64},
        'runtime_bundle': {'digest': 'b' * 64, 'destination': str(candidate), 'config_path': new_path,
            'source_config': '/staging/removed/openclaw.json', 'source_config_bytes': json.dumps(source).encode(),
            'config_bytes': b'changed' if fault == 'mapping' else mapped_body}}
    seen = []
    monkeypatch.setattr(module, '_validate_migration_recovery_context', lambda value: seen.append('context'))
    monkeypatch.setattr(custody, 'protected_tree_metadata', lambda path: seen.append(str(path)))
    monkeypatch.setattr(module._bundle, 'verify', lambda path, **kw: ({'plugins': ['plugins/0']}, kw['expected_digest']))
    monkeypatch.setattr(module.os.path, 'lexists', lambda path: fault != 'unpublished')
    def read(path, uid):
        assert uid == 501 and str(path) in (old_path, new_path)
        seen.append(str(path))
        if str(path) == old_path: return b'changed' if fault == 'old-drift' else b'old-config'
        return b'changed' if fault == 'versioned-drift' else mapped_body
    monkeypatch.setattr(module, '_configuration_bytes', read)
    if fault in ('old-drift', 'mapping', 'versioned-drift'):
        with pytest.raises(module.InstallError): module._verify_recovery_runtime(plan)
    else:
        module._verify_recovery_runtime(plan)
        assert old_path in seen
        assert (new_path in seen) is (fault != 'unpublished')
    assert not any('/staging/' in path for path in seen)


@pytest.mark.parametrize('fault', [None, 'source', 'preservation', 'services', 'runtime',
    'missing-key', 'rotated-key', 'dashboard-key', 'reused-key'])
def test_migration_publication_requalifies_artifacts_and_requires_persisted_credentials(monkeypatch, fault):
    body = json.dumps({'models': {'providers': {'ods-gateway': {'apiKey': 'c' * 64}}}}).encode()
    runtime = {'source': '/stage/runtime', 'digest': 'b' * 64, 'destination': '/protected/runtime',
        'source_config': '/stage/candidate/openclaw.json', 'source_config_bytes': body,
        'config_bytes': body, 'config_path': '/protected/versioned.json'}
    services = {'bundle': '/stage/services', 'expected_digest': 'd' * 64, 'expected_ref': 'e' * 40}
    preservation = {'fixture': 'approved'}
    plan = {'owner': SimpleNamespace(pw_name='owner'), 'key': ('a' * 64).encode(),
        'access_settings': {'gateway_port': 18789, 'install_dir': '/ods'},
        'source_environment': {'OPENCLAW_CONFIG_PATH': '/protected/old.json'},
        'migration_source_config_bytes': b'old', 'runtime_bundle': runtime, 'native_services': services,
        'migration_qualification': {'currentDigest': 'f' * 64, 'candidate': '/stage/candidate',
            'preservation': preservation}, 'upgrade_qualification': {'approved': True}}
    selected = {'runtime': dict(runtime, config_path='/protected/unversioned.json'), 'services': dict(services),
        'preservation': dict(preservation), 'previousConfigBytes': b'old', 'previousConfig': '/protected/old.json'}
    if fault == 'source': selected['previousConfigBytes'] = b'changed'
    if fault == 'preservation': selected['preservation'] = {'changed': True}
    if fault == 'services': selected['services']['expected_digest'] = '0' * 64
    if fault == 'runtime': selected['runtime']['config_bytes'] = b'changed'
    credentials = {'DASHBOARD_API_KEY': 'a' * 64, 'PIXEL_OPENWEBUI_KEY': 'b' * 64, 'PIXEL_MODEL_RELAY_KEY': 'c' * 64}
    if fault == 'missing-key': credentials.pop('PIXEL_MODEL_RELAY_KEY')
    if fault == 'rotated-key': credentials['PIXEL_MODEL_RELAY_KEY'] = 'd' * 64
    if fault == 'dashboard-key': credentials['DASHBOARD_API_KEY'] = 'd' * 64
    if fault == 'reused-key': credentials['PIXEL_OPENWEBUI_KEY'] = 'a' * 64
    events = []
    monkeypatch.setattr(module, '_validate_migration_recovery_context', lambda value: events.append('context'))
    monkeypatch.setattr(module, 'qualify_migration_selection', lambda **kw: events.append('selection') or selected)
    monkeypatch.setattr(module, '_env_file', lambda path: events.append('credentials') or credentials)
    monkeypatch.setattr(module, '_qualify_migration_ingress', lambda value: events.append('ingress'))
    if fault:
        with pytest.raises(module.InstallError): module._requalify_migration(plan)
    else:
        assert module._requalify_migration(plan) == {'approved': True}
    assert events[:2] == ['context', 'selection']


@pytest.mark.parametrize('case', ['plan', 'activate', 'nonroot', 'failure'])
def test_joint_cli_requires_explicit_activation_and_never_prints_private_plan(monkeypatch, capsys, case):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501 if case == 'nonroot' else 0)
    calls = []
    def plan(**kw):
        calls.append(('plan', kw))
        return {'private': 'never-print-this-key'}
    def activate(value, source):
        calls.append(('activate', source))
        assert value == {'private': 'never-print-this-key'}
        if case == 'failure': raise ValueError('never-print-this-key')
        return 'active'
    monkeypatch.setattr(module, 'make_migration_plan', plan)
    monkeypatch.setattr(module, 'migrate_install', activate)
    argv = ['migrate-native']
    args = {'source': '/source', 'install-dir': '/ods', 'owner': 'owner', 'candidate': '/candidate',
        'runtime-bundle': '/runtime', 'bundle-digest': 'a' * 64, 'current-bundle-digest': 'b' * 64,
        'services-bundle': '/services', 'services-digest': 'c' * 64, 'pixel-source-ref': 'd' * 40}
    for name, value in args.items(): argv.extend(['--' + name, value])
    if case != 'plan': argv.append('--activate')
    if case in ('activate', 'failure'):
        argv.extend(['--docker', '/docker', '--compose-project', 'ods',
            '--ingress-image', 'sha256:' + 'e' * 64, '--ingress-user', '501:20'])
    assert module.main(argv) == (1 if case in ('nonroot', 'failure') else 0)
    output = capsys.readouterr()
    assert 'never-print-this-key' not in output.out + output.err
    if case == 'nonroot': assert not calls
    elif case == 'plan':
        assert len(calls) == 1 and json.loads(output.out)['status'] == 'planned'
    else:
        assert [name for name, _ in calls] == ['plan', 'activate']
        if case == 'activate': assert json.loads(output.out)['status'] == 'active'


@pytest.mark.parametrize('fault', [None, 'missing', 'gateway', 'unmanaged', 'project', 'image',
    'user', 'stopped', 'duplicate', 'absent', 'inspect-error'])
def test_migration_ingress_requires_exact_managed_identity_and_runs_as_owner(monkeypatch, fault):
    owner = SimpleNamespace(pw_uid=501)
    transport = {'docker': '/docker', 'project': 'ods', 'image': 'sha256:' + 'a' * 64, 'user': '501:20'}
    env = module._native_transport_environment(transport, owner)
    if fault == 'gateway': env['PIXEL_HISTORY_PROJECT'] = 'other'
    plan = {'owner': owner, 'migration_qualification': {'transport': None if fault == 'missing' else transport},
        'gateway': {'ProgramArguments': ['/usr/bin/env', '-i',
            *(key + '=' + value for key, value in env.items()), '/usr/bin/true']}}
    context = {'cwd': '/', 'env': {'DOCKER_HOST': 'unix:///owner/docker.sock'},
        'user': 501, 'group': 20, 'extra_groups': []}
    monkeypatch.setattr(module, '_migration_edge_context', lambda plan: context)
    identity = 'b' * 64
    container = {'Id': identity, 'Image': transport['image'], 'State': {'Running': True},
        'Config': {'User': '501:20', 'Labels': {'com.docker.compose.project': 'ods',
            'com.docker.compose.service': 'pixel-native-ingress'}}}
    if fault == 'unmanaged': container['Config']['Labels'] = {}
    if fault == 'project': container['Config']['Labels']['com.docker.compose.project'] = 'other'
    if fault == 'image': container['Image'] = 'sha256:' + 'c' * 64
    if fault == 'user': container['Config']['User'] = '0:0'
    if fault == 'stopped': container['State']['Running'] = False
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert all(kwargs[key] == value for key, value in context.items())
        assert kwargs['capture_output'] is True and kwargs['timeout'] == 20
        if argv[1] == 'ps':
            assert 'label=com.docker.compose.project=ods' in argv
            assert 'label=com.docker.compose.service=pixel-native-ingress' in argv
            return SimpleNamespace(returncode=0, stdout='' if fault == 'absent' else
                (identity + '\n') * (2 if fault == 'duplicate' else 1))
        assert argv == ['/docker', 'inspect', identity]
        return SimpleNamespace(returncode=1 if fault == 'inspect-error' else 0, stdout=json.dumps([container]))
    monkeypatch.setattr(module.subprocess, 'run', run)
    if fault:
        with pytest.raises(module.InstallError): module._qualify_migration_ingress(plan)
    else:
        assert module._qualify_migration_ingress(plan) == identity
    if fault in ('missing', 'gateway'): assert not calls
