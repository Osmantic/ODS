import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('candidate_bundle',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-config.py')
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


@pytest.mark.parametrize('fault', [None, 'duplicate', 'wrong-root', 'error', 'missing'])
def test_relocated_probe_requires_unique_loaded_identity(tmp_path, monkeypatch, fault):
    path = str(tmp_path / 'bundle/plugins/0')
    item = {'id': 'pixel-ods', 'status': 'error' if fault == 'error' else 'loaded',
            'rootDir': '/ambient/plugin' if fault == 'wrong-root' else path}
    loaded = [] if fault == 'missing' else [item]
    if fault == 'duplicate': loaded.append(dict(item, rootDir='/ambient/plugin', status='error'))
    calls = []
    def command(args, **kwargs):
        calls.append(args[2:])
        assert kwargs['env']['OPENCLAW_SKIP_CHANNELS'] == '1'
        assert kwargs['env']['OPENCLAW_CONFIG_PATH'] == str(tmp_path / 'config.json')
        return json.dumps({'plugins': loaded}) if args[2:4] == ['plugins', 'list'] else ''
    monkeypatch.setattr(config.bootstrap, 'command', command)
    def run():
        config.validate_candidate(tmp_path / 'config.json', node=tmp_path / 'node',
            entrypoint=tmp_path / 'openclaw.mjs', expected={'pixel-ods': path},
            env={'HOME': str(tmp_path)}, cwd=tmp_path)
    if fault:
        with pytest.raises(ValueError, match='native-config-plugin-not-loaded'): run()
    else:
        run()
    assert calls == [['config', 'validate'], ['plugins', 'list', '--json']]


@pytest.mark.parametrize('fault', [None, 'root', 'duplicate', 'missing', 'foreign',
                                 'receipt', 'existing', 'load', 'mutation', 'config-change'])
def test_package_preserves_source_and_requires_relocated_loader(tmp_path, monkeypatch, fault):
    tmp_path = tmp_path.resolve()
    monkeypatch.setattr(config.sys, 'platform', 'darwin')
    monkeypatch.setattr(config.os, 'geteuid', lambda: 0 if fault == 'root' else 501)
    monkeypatch.setattr(config.bootstrap, 'selected_release', lambda *a: {'openclaw': '2026.6.33'})
    plugins = []
    for name in ('pixel-ods', 'searxng'):
        path = tmp_path / name
        path.mkdir()
        (path / 'openclaw.plugin.json').write_text(json.dumps({'id': name}))
        plugins.append(str(path))
    monkeypatch.setattr(config, 'runtime_plugins', lambda *a: {'searxng': plugins[1] if fault != 'foreign' else '/foreign'})
    candidate = tmp_path / 'candidate'
    candidate.mkdir(mode=0o700)
    receipt = candidate / 'candidate.json'
    receipt.write_text(json.dumps({'status': 'active' if fault == 'receipt' else 'staged',
                                  'pixelSourceRef': 'a' * 40, 'requiresServiceQualification': True}))
    receipt.chmod(0o600)
    original = {'plugins': {'allow': ['pixel-ods', 'searxng'],
                            'load': {'paths': plugins + ([plugins[0]] if fault == 'duplicate' else [])}}}
    if fault == 'missing': original['plugins']['allow'].append('discord')
    path = candidate / 'openclaw.json'
    path.write_text(json.dumps(original))
    path.chmod(0o600)
    calls = []
    def build(**kwargs):
        calls.append('build')
        assert kwargs['plugins'] == plugins
        assert kwargs['stream_progress_fix'] is True
        kwargs['destination'].mkdir()
        return 'b' * 64
    monkeypatch.setattr(config.bundle, 'build', build)
    def validate(path, **kwargs):
        calls.append('validate')
        relocated = json.loads(path.read_text())
        staged = kwargs['node'].parent
        assert kwargs['entrypoint'] == staged / 'runtime/openclaw.mjs'
        assert kwargs['env']['PATH'] == '/usr/bin:/bin:/usr/sbin:/sbin'
        assert relocated['plugins']['load']['paths'] == [str(staged / 'plugins/0'), str(staged / 'plugins/1')]
        assert kwargs['expected'] == dict(zip(['pixel-ods', 'searxng'], relocated['plugins']['load']['paths']))
        if fault == 'load': raise ValueError('loader failed')
        if fault == 'config-change':
            (candidate / 'openclaw.json').write_text('{}')
    monkeypatch.setattr(config, 'validate_candidate', validate)
    def verify(*args, **kwargs):
        calls.append('verify')
        assert kwargs['expected_digest'] == 'b' * 64
        if fault == 'mutation': raise ValueError('runtime changed')
    monkeypatch.setattr(config.bundle, 'verify', verify)
    destination = tmp_path / 'Qualified Bundle'
    if fault == 'existing': destination.mkdir()
    # Avoid making the output a descendant of the runtime fixture.
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    def run():
        return config.stage_bundle(source=tmp_path, ref='a' * 40, candidate=candidate,
            node=tmp_path / 'node', runtime=runtime, destination=destination)
    if fault:
        with pytest.raises(ValueError): run()
        assert destination.exists() is (fault == 'existing')
    else:
        assert run() == 'b' * 64
        assert calls == ['build', 'validate', 'verify']
        assert destination.is_dir()
    if fault != 'config-change': assert json.loads(path.read_text()) == original
    assert not list(tmp_path.glob('.pixel-package-*'))
