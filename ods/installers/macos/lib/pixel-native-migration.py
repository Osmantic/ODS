"""Preserve owner state while staging an existing native Pixel configuration.

This does not authorize or activate a runtime/configuration transition. New tool
definitions come from the shared renderer; explicit existing access-mode fields
are retained and still require protected access-policy verification at activation.
"""
import copy
import hashlib
import json
from pathlib import Path


CONTROLLED_AGENT_FIELDS = frozenset(('sandbox', 'tools', 'contextInjection', 'contextLimits',
    'contextTokens', 'bootstrapMaxChars', 'bootstrapTotalMaxChars'))
ACCESS_FIELDS = (('sandbox', 'mode'), ('tools', 'exec', 'host'), ('tools', 'exec', 'security'),
    ('tools', 'exec', 'ask'), ('tools', 'fs', 'workspaceOnly'))


def check_plugin_transition(previous, candidate):
    # Candidate plugins are subsequently qualified against the pinned runtime.
    # Never silently remove an existing plugin or restore its old runtime path.
    allowed, selected = previous.get('allow'), candidate.get('allow')
    entries = previous.get('entries', {})
    if (type(allowed) is not list or type(selected) is not list
            or any(type(value) is not str for value in allowed + selected)
            or len(set(allowed)) != len(allowed) or len(set(selected)) != len(selected)
            or 'pixel-ods' not in allowed or not set(allowed) <= set(selected)
            or previous.get('installs') or type(entries) is not dict
            or not set(entries) <= set(allowed)):
        raise ValueError('legacy-native-plugin-migration-required')


def verify_state_preservation(previous, candidate_bytes, record, *, state_dir):
    """Recheck data preservation independently of a candidate's self-report.

    The privileged caller must obtain previous/state_dir from the active trusted
    deployment, not from this owner-side record. Runtime and policy approval are
    separate; passing this check does not authorize activation.
    """
    fields = {'schemaVersion', 'kind', 'stateDir', 'workspace', 'agentId',
        'requiresJointActivation', 'previousConfigSha256', 'candidateConfigSha256'}
    if (type(record) is not dict or set(record) != fields or type(record['schemaVersion']) is not int
            or record['schemaVersion'] != 1 or record['kind'] != 'legacy-native'
            or record['agentId'] != 'pixel' or record['requiresJointActivation'] is not True
            or record['stateDir'] != str(state_dir)
            or record['previousConfigSha256'] != hashlib.sha256(json.dumps(previous,
                sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            or record['candidateConfigSha256'] != hashlib.sha256(candidate_bytes).hexdigest()):
        raise ValueError('native-migration-selection-changed')
    candidate = json.loads(candidate_bytes)
    try:
        old_agents = previous['agents']['list']
        new_agents = [agent for agent in candidate['agents']['list'] if agent.get('id') == 'pixel']
        if len(old_agents) != 1 or old_agents[0]['id'] != 'pixel' or len(new_agents) != 1:
            raise ValueError()
        old, new = old_agents[0], new_agents[0]
        old_defaults = previous['agents'].get('defaults', {})
        workspace = old.get('workspace') or old_defaults.get('workspace')
        if (not workspace or record['workspace'] != workspace or new.get('workspace') != workspace
                or new.get('agentDir') != old.get('agentDir')
                or new.get('model', candidate['agents'].get('defaults', {}).get('model')) !=
                   old.get('model', old_defaults.get('model'))
                or candidate['gateway']['auth'] != previous['gateway']['auth']
                or candidate['gateway']['port'] != previous['gateway']['port']
                or candidate.get('session', {}).get('store') != previous.get('session', {}).get('store')):
            raise ValueError()
        for name, value in previous['models']['providers'].items():
            if candidate['models']['providers'].get(name) != value:
                raise ValueError()
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError('native-migration-owner-state-changed') from None
    return {'stateDir': str(state_dir), 'workspace': record['workspace'],
        'previousConfigSha256': record['previousConfigSha256'],
        'candidateConfigSha256': record['candidateConfigSha256']}


def preserve_state(candidate, previous, *, state_dir):
    state_dir = Path(state_dir)
    if not state_dir.is_absolute() or state_dir.resolve(strict=True) != state_dir or not state_dir.is_dir():
        raise ValueError('existing-canonical-native-state-required')
    if not isinstance(previous, dict) or not isinstance(candidate, dict):
        raise ValueError('native-migration-configurations-required')
    if any(not isinstance(previous.get(key), dict) for key in ('plugins', 'agents', 'models', 'gateway')):
        raise ValueError('native-migration-configuration-shape-invalid')
    plugins = previous.get('plugins', {})
    candidate_plugins = candidate.get('plugins')
    if type(candidate_plugins) is not dict:
        raise ValueError('native-migration-configuration-shape-invalid')
    check_plugin_transition(plugins, candidate_plugins)
    old_agents = previous.get('agents', {})
    agents = old_agents.get('list', [])
    if (not isinstance(agents, list) or len(agents) != 1 or not isinstance(agents[0], dict)
            or agents[0].get('id') != 'pixel'):
        raise ValueError('legacy-native-pixel-agent-required')
    defaults = old_agents.get('defaults', {})
    if not isinstance(defaults, dict) or not isinstance(previous['models'].get('providers'), dict):
        raise ValueError('native-migration-configuration-shape-invalid')
    old = agents[0]
    workspace = old.get('workspace') or defaults.get('workspace')
    if not isinstance(workspace, str) or not Path(workspace).is_absolute() or not Path(workspace).is_dir():
        raise ValueError('existing-native-workspace-required')
    model = old.get('model') or defaults.get('model')
    if model != 'ods-gateway/ods/current':
        raise ValueError('legacy-native-model-selection-needs-review')
    provider = previous.get('models', {}).get('providers', {}).get('ods-gateway')
    if not isinstance(provider, dict) or not provider.get('models'):
        raise ValueError('existing-native-model-provider-required')
    old_gateway = previous.get('gateway', {})
    auth = old_gateway.get('auth', {})
    if (not isinstance(auth, dict) or auth.get('mode', 'token') != 'token' or auth.get('password')
            or not isinstance(auth.get('token'), str) or not auth['token'] or old_gateway.get('bind') != 'loopback'
            or old_gateway.get('port') != candidate.get('gateway', {}).get('port')):
        raise ValueError('legacy-native-gateway-selection-needs-review')
    result = copy.deepcopy(candidate)
    for key, value in previous.items():
        if key not in ('agents', 'gateway', 'models', 'plugins', 'tools'):
            result[key] = copy.deepcopy(value)
    result['gateway']['auth'] = copy.deepcopy(old_gateway['auth'])
    if 'controlUi' in old_gateway:
        result['gateway']['controlUi'] = copy.deepcopy(old_gateway['controlUi'])
    result['models'].setdefault('providers', {}).update(copy.deepcopy(previous['models']['providers']))
    for key, value in previous['models'].items():
        if key != 'providers': result['models'][key] = copy.deepcopy(value)
    target_defaults = result['agents'].setdefault('defaults', {})
    for key, value in defaults.items():
        if key not in CONTROLLED_AGENT_FIELDS:
            target_defaults[key] = copy.deepcopy(value)
    targets = [agent for agent in result['agents']['list'] if agent['id'] == 'pixel']
    if len(targets) != 1:
        raise ValueError('generated-native-pixel-agent-required')
    target = targets[0]
    for key, value in old.items():
        if key not in CONTROLLED_AGENT_FIELDS:
            target[key] = copy.deepcopy(value)
    # Preserve the owner's selected access mode, not the legacy tool allowlist
    # or sandbox image. Resetting these fields breaks receipt relocation and
    # silently changes an existing full-access installation into sandbox mode.
    for path in ACCESS_FIELDS:
        source = old
        for key in path:
            if not isinstance(source, dict) or key not in source:
                break
            source = source[key]
        else:
            destination = target
            for key in path[:-1]: destination = destination.setdefault(key, {})
            destination[path[-1]] = copy.deepcopy(source)
    target['workspace'] = workspace
    # Default session/auth locations depend on the state root, not the workspace.
    contract = {'schemaVersion': 1, 'kind': 'legacy-native', 'stateDir': str(state_dir),
        'workspace': workspace, 'agentId': 'pixel', 'requiresJointActivation': True}
    return result, contract
