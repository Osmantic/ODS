import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_onboarding',
    ROOT / 'installers/macos/lib/pixel-native-onboarding.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('fault', [None, 'external', 'reasoning', 'preserve', 'context', 'key', 'duplicate', 'search',
    'parallel', 'parallel-default', 'migration', 'legacy-key', 'legacy-conflict', 'legacy-remote'])
def test_installed_contract_uses_shared_onboarding_without_model_reset(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    bootstrap, env_helper = module.helper('bootstrap'), module.helper('env')
    monkeypatch.setattr(bootstrap, 'selected_release', lambda *a: {})
    calls = []
    original_command = bootstrap.command
    def command(args, **kw):
        calls.append(args)
        assert 'd' * 64 not in ' '.join(args)
        if args[0] != '/bin/bash': return 'a' * 64
        return original_command(args, **kw)
    monkeypatch.setattr(bootstrap, 'command', command)
    monkeypatch.setattr(module, 'helper', lambda name: bootstrap if name == 'bootstrap' else env_helper)
    path = tmp_path / '.env'
    context = 'bad' if fault == 'context' else '16384'
    body = 'GGUF_FILE=owner-model.gguf\nLLM_MODEL=friendly-name\nCTX_SIZE=' + context + '\n'
    if fault != 'legacy-key':
        body += 'PIXEL_MODEL_RELAY_KEY=' + ('missing' if fault == 'key' else 'd' * 64) + '\n'
    body += 'PIXEL_MODEL_RELAY_PORT=4106\nPIXEL_NATIVE_GATEWAY_PORT=18799\nSEARXNG_PORT=8898\n'
    if fault == 'external': body += 'EXTERNAL_LLM_URL=https://example.test/v1\nEXTERNAL_LLM_MODEL=owner-remote\n'
    if fault == 'reasoning': body += 'LLAMA_REASONING=on\n'
    if fault == 'duplicate': body += 'CTX_SIZE=8192\n'
    if fault != 'parallel-default':
        body += 'PIXEL_WEB_SEARCH_PROVIDER=' + ('invalid' if fault == 'search' else
            'parallel-free' if fault == 'parallel' else 'searxng') + '\n'
    search = module.search_helper(ROOT)
    monkeypatch.setattr(search, 'prepare', lambda path: {'path': str(path / ('parallel-' + search.VERSION))})
    monkeypatch.setattr(module, 'search_helper', lambda root: search)
    path.write_text(body)
    path.chmod(0o600)
    observer = tmp_path / 'extensions/services/pixel-agent/host/system_observe.py'
    observer.parent.mkdir(parents=True)
    observer.write_bytes((ROOT / 'extensions/services/pixel-agent/host/system_observe.py').read_bytes())
    observer.chmod(0o600)
    node = tmp_path / 'node'
    node.touch()
    destination = tmp_path / 'onboarding.json'
    previous_config = None
    if isinstance(fault, str) and fault.startswith('legacy-'):
        previous_config = tmp_path / 'previous.json'
        previous_config.write_text(json.dumps({'models': {'providers': {'ods-gateway': {
            'baseUrl': 'http://remote.example/v1' if fault == 'legacy-remote' else 'http://127.0.0.1:4106/v1',
            'apiKey': ('e' if fault == 'legacy-conflict' else 'd') * 64}}}}))
        previous_config.chmod(0o600)
    workspace = tmp_path / "Owner's existing projects"
    if fault == 'migration': workspace.mkdir()
    def run():
        return module.write(source=tmp_path, ref='b' * 40, ods_source=ROOT, install_dir=tmp_path,
            home=tmp_path / 'Owner Home', runtime=tmp_path / 'runtime', node=node, destination=destination,
            workspace=workspace if fault == 'migration' else None, previous_config=previous_config)
    if fault in ('context', 'key', 'duplicate', 'search', 'legacy-conflict', 'legacy-remote'):
        with pytest.raises(ValueError): run()
        assert not destination.exists() and not calls
    else:
        assert run() == destination
        value = json.loads(destination.read_text())
        if fault == 'migration':
            policy = json.loads((tmp_path / 'operations-policy.json').read_text())
            assert policy['targets']['ods-host']['writableRoots'] == [str(workspace)]
        assert value['modelId'] == 'ods/current'
        assert value['modelName'] == 'ODS Current (' + ('owner-remote' if fault == 'external' else 'owner-model.gguf') + ')'
        assert value['modelContextWindow'] == 16384 and value['modelMaxTokens'] == 4096
        assert value['modelReasoning'] is (fault == 'reasoning')
        assert value['modelBaseUrl'] == 'http://127.0.0.1:4106/v1'
        assert value['webSearchProvider'] == ('parallel-free' if fault in ('parallel', 'parallel-default') else 'searxng')
        if fault in ('parallel', 'parallel-default'):
            assert any(item['id'] == 'parallel' for item in value['gatewayExtensions'])
        policy = json.loads((tmp_path / 'operations-policy.json').read_text())
        assert policy['schemaVersion'] == 2 and 'host.os-release' in policy['actions']
        assert 'ods.extensions.install' in policy['actions']
        assert policy['actions']['host.os-release']['targets'] == ['ods-host']
        assert destination.stat().st_mode & 0o777 == 0o600
        if fault == 'preserve':
            value['modelMaxTokens'] = 2048
            destination.write_text(json.dumps(value))
            run()
            assert json.loads(destination.read_text())['modelMaxTokens'] == 2048
    assert path.read_text() == body
    assert not list(tmp_path.glob('.pixel-gateway-key.*'))
