import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('native_config',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-config.py')
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


@pytest.mark.parametrize('fault', ['name', 'version', 'symlink', 'archive'])
def test_parallel_packaging_rejects_unverified_source(tmp_path, fault):
    spec = importlib.util.spec_from_file_location('native_test_search',
        Path(__file__).resolve().parents[1] / 'extensions/services/pixel-agent/host/native_search.py')
    search = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(search)
    path = tmp_path / ('wrong-name' if fault == 'name' else 'parallel-' + search.VERSION)
    if fault == 'symlink':
        target = tmp_path / 'outside'
        target.mkdir()
        path.symlink_to(target, target_is_directory=True)
    else:
        path.mkdir()
    archive = tmp_path / ('parallel-' + search.VERSION + '.tgz')
    archive.write_bytes(b'not-the-pinned-package')
    archive.chmod(0o600)
    with pytest.raises((ValueError, RuntimeError)):
        config.verified_parallel_plugin(path, {'openclaw': 'wrong-version' if fault == 'version' else search.VERSION})


def test_environment_parser_preserves_shell_quoted_data_without_execution(tmp_path):
    path = tmp_path / '.env'
    path.write_text("# generated\nPIXEL_NAME='Owner with spaces'\nPIXEL_VALUE='$(touch /never-execute)'\nPIXEL_QUOTE='can'\\''t'\n")
    assert config.generated_environment(path) == {'PIXEL_NAME': 'Owner with spaces',
        'PIXEL_VALUE': '$(touch /never-execute)', 'PIXEL_QUOTE': "can't"}


@pytest.mark.parametrize('body', ['KEY=a KEY=b', 'bad-key=x', 'KEY=x;touch /tmp/bad'])
def test_environment_parser_rejects_non_assignment_tokens(tmp_path, body):
    path = tmp_path / '.env'
    path.write_text(body)
    with pytest.raises(ValueError): config.generated_environment(path)


@pytest.mark.parametrize('fault', [None, 'existing-config', 'renderer', 'root', 'plugin-load', 'schema', 'overlay'])
def test_candidate_uses_upstream_policy_without_activating_services(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(config.sys, 'platform', 'darwin')
    monkeypatch.setattr(config.os, 'geteuid', lambda: 0 if fault == 'root' else 501)
    monkeypatch.setattr(config.bootstrap, 'selected_release', lambda *a: {})
    paths = {'pixel-source-broker': str(tmp_path / 'plugin'),
             'pixel-operations-broker': str(tmp_path / 'plugin-ops'),
             'pixel-frontier-broker': str(tmp_path / 'plugin-frontier'),
             'searxng': str(tmp_path / 'searxng')}
    monkeypatch.setattr(config, 'runtime_plugins', lambda *a: paths)
    overlays = []
    def overlay(path, **kwargs):
        overlays.append(kwargs)
        if fault == 'overlay': raise ValueError('invalid runtime overlay')
    monkeypatch.setattr(config, 'apply_runtime_budget', overlay)
    node = tmp_path / 'node'
    node.touch()
    home = tmp_path / 'New Owner Home'
    home.mkdir()
    if fault == 'existing-config': (home / 'openclaw.json').write_text('existing')
    answers = tmp_path / 'answers.json'
    policy = tmp_path / 'policy.json'
    policy.write_text('{"schemaVersion":2,"targets":{},"actions":{}}')
    policy.chmod(0o600)
    answers.write_text(json.dumps({'agentId': 'pixel', 'deploymentName': 'ods-default',
        'openclawHome': str(home), 'gatewayPort': 19800, 'operationsLimbEnabled': True,
        'gatewayExtensions': [], 'operationsPolicyFile': str(policy)}))
    answers.chmod(0o600)
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        if args[:2] == ['git', 'clone']:
            Path(args[-1]).mkdir()
        elif args[0] == str(node) and args[1].endswith('configure.mjs'):
            root = Path(args[1]).parent.parent
            (root / '.env').write_text("PIXEL_LIMB_OPERATIONS_ENABLED='1'\n")
            generated = root / '.generated'
            (generated / 'workspace').mkdir(parents=True)
            (generated / 'workspace/AGENTS.md').write_text('upstream instructions')
            (generated / 'ops-policy.json').write_text('{"originalPolicy":true}')
        elif args[0] == str(node) and args[1].endswith('render-config.mjs'):
            assert args[1].endswith('render-config.mjs')
            assert kwargs['env']['PIXEL_LIMB_OPERATIONS_ENABLED'] == '1'
            assert kwargs['env']['PIXEL_OPS_PLUGIN_PATH'] == str(tmp_path / 'plugin-ops')
            assert kwargs['env']['OPENCLAW_HOME'] != str(home)
            if fault == 'renderer': raise ValueError('private upstream error')
            Path(args[2]).write_text(json.dumps({'gateway': {'http': {'endpoints': {}}},
                'plugins': {'allow': ['searxng', 'pixel-operations-broker'], 'load': {'paths': []}},
                'tools': {'alsoAllow': ['pixel_ops_run', 'write', 'edit']}}))
        elif args[2:4] == ['plugins', 'list']:
            return json.dumps({'plugins': [{'id': key, 'rootDir': path, 'status': 'error' if fault == 'plugin-load' else 'loaded'}
                for key, path in paths.items()]})
        elif args[0] == str(node):
            assert args[2:] == ['config', 'validate']
            if fault == 'schema': raise ValueError('invalid configuration')
        return ''
    monkeypatch.setattr(config.bootstrap, 'command', command)
    destination = tmp_path / 'candidate'
    def run():
        return config.prepare(source=tmp_path, ref='a' * 40, answers=answers, node=node,
                              sandbox_image='sha256:' + 'b' * 64, destination=destination, runtime=tmp_path)
    if fault:
        with pytest.raises(ValueError): run()
        assert not destination.exists()
    else:
        assert run() == destination
        assert overlays[0]['openclaw_home'] == home
        assert overlays[0]['research_port'] == 3004
        value = json.loads((destination / 'openclaw.json').read_text())
        assert value['tools']['alsoAllow'] == ['pixel_ops_run', 'write', 'edit']
        assert value['gateway']['port'] == 19800
        assert value['plugins']['entries']['pixel-ods']['config']['workspacePreviewTransport'] == 'docker-desktop'
        assert (destination / 'openclaw.json').stat().st_mode & 0o777 == 0o600
        assert (destination / 'workspace/AGENTS.md').read_text() == 'upstream instructions'
        assert not (home / 'openclaw.json').exists()
        assert not list(destination.glob('*.service'))
    assert not list(tmp_path.glob('.pixel-config-*'))
    if fault in ('root', 'existing-config'): assert not calls


@pytest.mark.parametrize('migrating', [False, True])
def test_existing_home_is_preserved_and_allowed_only_for_explicit_migration(tmp_path, monkeypatch, migrating):
    monkeypatch.setattr(config.sys, 'platform', 'darwin')
    monkeypatch.setattr(config.os, 'geteuid', lambda: 501)
    monkeypatch.setattr(config.bootstrap, 'selected_release', lambda *args: {})
    monkeypatch.setattr(config, 'runtime_plugins', lambda *args: {})
    home = tmp_path / 'existing-home'
    home.mkdir()
    existing = home / 'openclaw.json'
    existing.write_text('{"existing":true}')
    existing.chmod(0o600)
    monkeypatch.setattr(config, 'private_answers', lambda path: {'openclawHome': str(home)})
    calls = []

    def stop_before_renderer(args, **kwargs):
        calls.append(args)
        raise RuntimeError('isolated-rendering-reached')

    monkeypatch.setattr(config.bootstrap, 'command', stop_before_renderer)
    migration = {'previous_config': existing, 'previous_state_dir': home} if migrating else {}
    expected = 'isolated-rendering-reached' if migrating else 'initial-native-config-requires-unconfigured-home'
    with pytest.raises((RuntimeError, ValueError), match=expected):
        config.prepare(source=tmp_path, ref='a' * 40, answers=tmp_path / 'answers.json', node=existing,
            sandbox_image='sha256:' + 'b' * 64, destination=tmp_path / 'candidate', runtime=tmp_path,
            **migration)
    assert bool(calls) == migrating
    assert existing.read_text() == '{"existing":true}'
    assert existing.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / 'candidate').exists()


@pytest.mark.parametrize('result_kind', ['valid', 'unchanged', 'outside', 'symlink', 'public'])
def test_overlay_accepts_only_private_sibling_and_passes_explicit_home(tmp_path, monkeypatch, result_kind):
    path = tmp_path / 'openclaw.json'
    path.write_text('{"before":true}')
    staged = tmp_path / '.ods-pixel-runtime-budget.test'
    staged.write_text('{"after":true}')
    staged.chmod(0o600)
    home = tmp_path / 'Owner Home' / '.openclaw'
    if result_kind == 'public': staged.chmod(0o644)
    if result_kind == 'symlink':
        staged.unlink()
        staged.symlink_to(path)
    def command(args, **kwargs):
        assert args[1].endswith('/installers/lib/pixel-runtime-budget.py')
        assert args[-3:] == ['3099', str(tmp_path / 'answers'), str(home)]
        return {'outside': '/elsewhere/.ods-pixel-runtime-budget.test',
                'unchanged': 'unchanged'}.get(result_kind, str(staged)) + '\n'
    monkeypatch.setattr(config.bootstrap, 'command', command)
    def run():
        config.apply_runtime_budget(path, answers=tmp_path / 'answers',
                                    openclaw_home=home, research_port=3099, env={})
    if result_kind in ('outside', 'symlink', 'public'):
        with pytest.raises((ValueError, OSError)): run()
        assert json.loads(path.read_text()) == {'before': True}
    else:
        run()
        assert json.loads(path.read_text()) == ({'after': True} if result_kind == 'valid' else {'before': True})


@pytest.mark.parametrize('fault', [None, 'runtime', 'plugin-version', 'plugin-id', 'escape'])
def test_runtime_paths_require_selected_plugin_versions(tmp_path, fault):
    runtime = tmp_path / 'runtime'
    package = runtime / 'node_modules/openclaw/package.json'
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({'name': 'openclaw', 'version': 'wrong' if fault == 'runtime' else '2026.6.33'}))
    release = {'openclaw': '2026.6.33', 'pixel': '4.3.27', 'openclawPlugins': {}}
    for _, plugin_id, name in config.bootstrap.PLUGINS:
        release['openclawPlugins'][name] = '2026.6.33'
        path = runtime / 'node_modules' / name
        path.mkdir(parents=True)
        (path / 'package.json').write_text(json.dumps({'version': '2026.6.33'}))
        (path / 'openclaw.plugin.json').write_text(json.dumps({'id': plugin_id}))
    for directory, plugin_id in config.bootstrap.PIXEL_PLUGINS:
        path = runtime / 'pixel-plugins' / directory
        path.mkdir(parents=True)
        (path / 'package.json').write_text(json.dumps({'version': 'wrong' if fault == 'plugin-version' else '4.3.27'}))
        (path / 'openclaw.plugin.json').write_text(json.dumps({'id': 'wrong' if fault == 'plugin-id' else plugin_id}))
    if fault == 'escape':
        path = runtime / 'pixel-plugins/plugin-ops'
        target = tmp_path / 'elsewhere'
        path.rename(target)
        path.symlink_to(target, target_is_directory=True)
    if fault:
        with pytest.raises(ValueError): config.runtime_plugins(runtime, release)
    else:
        paths = config.runtime_plugins(runtime, release)
        assert len(paths) == 6
        assert paths['pixel-operations-broker'] == str(runtime / 'pixel-plugins/plugin-ops')


def test_operations_policy_canonicalizes_local_roots_without_changing_authority(tmp_path):
    real = tmp_path / 'physical'
    real.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(real, target_is_directory=True)
    policy = {'targets': {
        'mac': {'backend': 'local', 'defaultCwd': str(alias), 'allowedRoots': [str(alias)],
                'writableRoots': [str(alias / 'workspace')], 'allowRaw': False},
        'remote': {'backend': 'ssh', 'defaultCwd': '/var/jobs', 'allowedRoots': ['/var/jobs']}},
        'actions': {'inspect': {'targets': ['mac'], 'cwd': str(alias), 'argv': ['/bin/pwd'],
                                'defaultAuthority': 'observe'}},
        'authority': {'defaultLevel': 'propose'}, 'download': {'stagingRoot': str(alias / 'downloads')}}
    result = config.canonical_operations_policy(policy)
    assert result['targets']['mac']['defaultCwd'] == str(real)
    assert result['targets']['mac']['allowedRoots'] == [str(real)]
    assert result['targets']['remote'] == policy['targets']['remote']
    assert result['actions']['inspect']['cwd'] == str(real)
    assert result['actions']['inspect']['argv'] == ['/bin/pwd']
    assert result['authority'] == policy['authority']
    assert result['targets']['mac']['allowRaw'] is False
    assert policy['targets']['mac']['defaultCwd'] == str(alias)
    policy['actions']['inspect']['targets'].append('remote')
    with pytest.raises(ValueError, match='mixed-local-remote'):
        config.canonical_operations_policy(policy)
