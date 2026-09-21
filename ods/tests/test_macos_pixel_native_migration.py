import copy
import importlib.util
import hashlib
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location('native_migration',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-migration.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('fault', [None, 'full-access', 'plugins', 'agents', 'model', 'provider', 'port', 'auth', 'workspace', 'state'])
def test_migration_keeps_owner_state_without_restoring_legacy_tool_policy(tmp_path, fault):
    state, workspace = tmp_path / 'state', tmp_path / 'owner-projects'
    state.mkdir()
    workspace.mkdir()
    (workspace / 'snake.html').write_text('owner project')
    (state / 'sessions.json').write_text('retained')
    previous = {
        'plugins': {'allow': ['pixel-ods'], 'entries': {'pixel-ods': {}}},
        'agents': {'defaults': {'workspace': str(workspace), 'heartbeat': {'every': '0m'}},
            'list': [{'id': 'pixel', 'model': 'ods-gateway/ods/current', 'name': 'Owner Name',
                'params': {'temperature': 0.3}, 'tools': {'allow': ['old-tool']}, 'sandbox': {'mode': 'off'}}]},
        'models': {'providers': {'ods-gateway': {'apiKey': 'owner-key', 'models': [{'id': 'ods/current'}]}}},
        'gateway': {'port': 18789, 'bind': 'loopback', 'auth': {'mode': 'token', 'token': 'keep-token'}},
        'session': {'store': str(state / 'sessions.json')}, 'logging': {'level': 'warn'},
        'tools': {'allow': ['old-tool']}}
    candidate = {'plugins': {'allow': ['pixel-ods', 'pixel-operations-broker']},
        'agents': {'defaults': {'sandbox': {'mode': 'all'}, 'workspace': '/unused'},
            'list': [{'id': 'pixel', 'sandbox': {'mode': 'all'}, 'tools': {'alsoAllow': ['pixel_ops_run']}}]},
        'models': {'providers': {}}, 'gateway': {'port': 18789, 'auth': {'mode': 'token', 'token': 'new'}},
        'tools': {'alsoAllow': ['pixel_ops_run']}}
    if fault == 'full-access':
        previous['agents']['list'][0]['tools'].update(exec={'host': 'gateway', 'security': 'full', 'ask': 'off'},
            fs={'workspaceOnly': False})
    if fault == 'plugins': previous['plugins']['allow'].append('custom')
    if fault == 'agents': previous['agents']['list'].append({'id': 'custom'})
    if fault == 'model': previous['agents']['list'][0]['model'] = 'other/model'
    if fault == 'provider': previous['models']['providers'] = {}
    if fault == 'port': candidate['gateway']['port'] = 18889
    if fault == 'auth': previous['gateway']['auth']['mode'] = 'password'
    if fault == 'workspace': previous['agents']['defaults']['workspace'] = str(tmp_path / 'missing')
    if fault == 'state': state = tmp_path / 'missing-state'
    before_old, before_new = copy.deepcopy(previous), copy.deepcopy(candidate)
    if fault not in (None, 'full-access'):
        with pytest.raises((ValueError, OSError)): module.preserve_state(candidate, previous, state_dir=state)
    else:
        merged, contract = module.preserve_state(candidate, previous, state_dir=state)
        assert merged['gateway']['auth'] == previous['gateway']['auth']
        assert merged['models'] == previous['models']
        assert merged['session'] == previous['session']
        assert merged['logging'] == previous['logging']
        agent = merged['agents']['list'][0]
        assert agent['workspace'] == str(workspace) and agent['name'] == 'Owner Name'
        assert agent['params'] == {'temperature': 0.3}
        assert merged['plugins'] == candidate['plugins'] and merged['tools'] == candidate['tools']
        assert agent['sandbox'] == {'mode': 'off'}
        expected_tools = {'alsoAllow': ['pixel_ops_run']}
        if fault == 'full-access':
            expected_tools.update(exec={'host': 'gateway', 'security': 'full', 'ask': 'off'}, fs={'workspaceOnly': False})
        assert agent['tools'] == expected_tools
        assert contract['requiresJointActivation'] is True and contract['stateDir'] == str(state)
        assert 'owner-key' not in str(contract) and 'keep-token' not in str(contract)
        assert (state / 'sessions.json').read_text() == 'retained'
        assert (workspace / 'snake.html').read_text() == 'owner project'
    assert previous == before_old and candidate == before_new


@pytest.mark.parametrize('fault', [None, 'key', 'provider', 'workspace', 'state', 'model', 'store',
    'agent-dir', 'port', 'hash', 'previous', 'extra'])
def test_preservation_verifier_checks_values_even_when_candidate_hash_is_recomputed(fault):
    previous = {'agents': {'defaults': {}, 'list': [{'id': 'pixel', 'workspace': '/owner/work',
        'model': 'ods-gateway/ods/current'}]}, 'models': {'providers': {'ods-gateway': {'apiKey': 'keep'}}},
        'gateway': {'port': 18789, 'auth': {'mode': 'token', 'token': 'keep'}}, 'session': {}}
    candidate = copy.deepcopy(previous)
    record = {'schemaVersion': 1, 'kind': 'legacy-native', 'stateDir': '/owner/state',
        'workspace': '/owner/work', 'agentId': 'pixel', 'requiresJointActivation': True,
        'previousConfigSha256': hashlib.sha256(json.dumps(previous, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()}
    if fault == 'key': candidate['gateway']['auth']['token'] = 'changed'
    if fault == 'provider': candidate['models']['providers']['ods-gateway']['apiKey'] = 'changed'
    if fault == 'workspace':
        candidate['agents']['list'][0]['workspace'] = '/other'
        record['workspace'] = '/other'
    if fault == 'state': record['stateDir'] = '/other'
    if fault == 'model': candidate['agents']['list'][0]['model'] = 'another/model'
    if fault == 'store': candidate['session']['store'] = '/other/sessions.json'
    if fault == 'agent-dir': candidate['agents']['list'][0]['agentDir'] = '/other/agent'
    if fault == 'port': candidate['gateway']['port'] = 18889
    if fault == 'previous': record['previousConfigSha256'] = '0' * 64
    if fault == 'extra': record['skipChecks'] = True
    body = json.dumps(candidate).encode()
    record['candidateConfigSha256'] = '0' * 64 if fault == 'hash' else hashlib.sha256(body).hexdigest()
    if fault:
        with pytest.raises(ValueError):
            module.verify_state_preservation(previous, body, record, state_dir='/owner/state')
    else:
        result = module.verify_state_preservation(previous, body, record, state_dir='/owner/state')
        assert result['workspace'] == '/owner/work' and result['stateDir'] == '/owner/state'
        assert 'keep' not in str(result)
