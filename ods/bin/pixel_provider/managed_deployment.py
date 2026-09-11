"""Pure inputs for the protected managed-gateway environment transaction.

Syntax validation is NOT filesystem custody, runtime qualification, installation,
or activation. The caller must qualify executable/source paths, preserve the
previous environment, and hold admission across its journaled service restart.
Provider enforcement is independent of the Full Access/Safer drop-in.
"""

import json
import posixpath

from .activation_config import _binding
from .store import StoreError

# OpenClaw 2026.6.33 with the patches in runtime-source/source-lock.json.
KNOWN_HOOKS = frozenset({
    'before_model_resolve', 'agent_turn_prepare', 'before_prompt_build',
    'before_agent_start', 'before_agent_reply', 'model_call_started',
    'model_call_ended', 'llm_input', 'llm_output', 'before_agent_finalize',
    'agent_end', 'before_compaction', 'after_compaction', 'before_reset',
    'inbound_claim', 'message_received', 'message_sending',
    'reply_payload_sending', 'message_sent', 'before_tool_call',
    'after_tool_call', 'tool_result_persist', 'before_message_write',
    'session_start', 'session_end', 'subagent_spawning',
    'subagent_delivery_target', 'subagent_spawned', 'subagent_ended',
    'deactivate', 'gateway_start', 'gateway_stop',
    'heartbeat_prompt_contribution', 'cron_changed', 'before_dispatch',
    'reply_dispatch', 'before_install', 'before_agent_run',
    'before_command_run', 'resolve_exec_env',
})
REQUIRED_MANAGED_HOOKS = (
    'before_model_resolve', 'before_agent_run', 'agent_end',
    'before_command_run', 'before_tool_call', 'after_tool_call', 'gateway_stop',
)
DEPLOYMENT_KEYS = frozenset({
    'binding', 'sourceRoot', 'hostPython', 'providerDirectory', 'ownerScopes',
    'leaseTimeoutSeconds', 'approvalTimeoutSeconds',
})
MAX_BYTES = 16384


def _units(value, code):
    # Match JavaScript String.length and reject lone surrogates before OS use.
    try:
        return len(value.encode('utf-16-le')) // 2
    except UnicodeEncodeError:
        raise StoreError(code) from None


def _encoded(value, code):
    try:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False,
                         separators=(',', ':'), sort_keys=True).encode('ascii')
    except (TypeError, ValueError, RecursionError):
        raise StoreError(code) from None
    if len(raw) > MAX_BYTES:
        raise StoreError(code)
    return raw


def _policy(value):
    code = 'invalid-managed-policy'
    if (type(value) is not dict or set(value) != {'version', 'plugins'}
            or type(value['version']) is not int or value['version'] != 1
            or type(value['plugins']) is not list or not 1 <= len(value['plugins']) <= 64):
        raise StoreError(code)
    plugins, seen = [], set()
    for item in value['plugins']:
        if type(item) is not dict or set(item) != {'id', 'hooks'}:
            raise StoreError(code)
        name, hooks = item['id'], item['hooks']
        if (type(name) is not str or not 1 <= _units(name, code) <= 256
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in name)
                or name in seen or type(hooks) is not list or not 1 <= len(hooks) <= 64
                or any(type(hook) is not str or hook not in KNOWN_HOOKS for hook in hooks)
                or len(hooks) != len(set(hooks))):
            raise StoreError(code)
        seen.add(name)
        plugins.append({'id': name, 'hooks': list(hooks)})
    result = {'version': 1, 'plugins': plugins}
    _encoded(result, code)
    return result


def required_policy(previous=None):
    """Preserve existing requirements; append the complete managed-hook union."""
    policy = _policy(previous) if previous is not None else {'version': 1, 'plugins': []}
    pixel = next((p for p in policy['plugins'] if p['id'] == 'pixel-ods'), None)
    if pixel is None:
        pixel = {'id': 'pixel-ods', 'hooks': []}
        policy['plugins'].append(pixel)
    pixel['hooks'].extend(hook for hook in REQUIRED_MANAGED_HOOKS if hook not in pixel['hooks'])
    return _policy(policy)


def _path(value):
    if (type(value) is not str or not 1 < _units(value, 'invalid-managed-deployment') <= 4096
            or not value.startswith('/') or value.startswith('//')
            or posixpath.normpath(value) != value or '\\' in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise StoreError('invalid-managed-deployment')
    return value


def deployment(binding, source_root, host_python, provider_directory, owner_scopes,
               lease_timeout_seconds=180, approval_timeout_seconds=60):
    """Construct a detached, bounded bootstrap document; grant no authority."""
    code = 'invalid-managed-deployment'
    if (type(binding) is not dict
            or set(binding) != {'schemaVersion', 'activationId', 'revision', 'allowCloud'}
            or type(binding['schemaVersion']) is not int or binding['schemaVersion'] != 1
            or type(owner_scopes) is not bool
            or type(lease_timeout_seconds) is not int or not 1 <= lease_timeout_seconds <= 3600
            or type(approval_timeout_seconds) is not int or not 1 <= approval_timeout_seconds <= 120
            or lease_timeout_seconds <= approval_timeout_seconds):
        raise StoreError(code)
    try:
        checked = _binding(binding['revision'], binding['allowCloud'], binding['activationId'])
    except StoreError:
        raise StoreError(code) from None
    result = {
        'binding': checked, 'sourceRoot': _path(source_root), 'hostPython': _path(host_python),
        'providerDirectory': _path(provider_directory), 'ownerScopes': owner_scopes,
        'leaseTimeoutSeconds': lease_timeout_seconds, 'approvalTimeoutSeconds': approval_timeout_seconds,
    }
    _encoded(result, code)
    return result


def environment_bytes(deployment_document, policy):
    """Serialize only two variables using systemd EnvironmentFile quoting.

The result is NOT shell source. Dollar signs and backticks are literal in a
systemd EnvironmentFile. Its private root-owned path and service drop-in belong
to the caller's reversible transaction, not to editable user preferences.
"""
    if type(deployment_document) is not dict or set(deployment_document) != DEPLOYMENT_KEYS:
        raise StoreError('invalid-managed-deployment')
    doc = deployment(
        deployment_document['binding'], deployment_document['sourceRoot'],
        deployment_document['hostPython'], deployment_document['providerDirectory'],
        deployment_document['ownerScopes'], deployment_document['leaseTimeoutSeconds'],
        deployment_document['approvalTimeoutSeconds'],
    )
    checked = _policy(policy)
    pixel = next((p for p in checked['plugins'] if p['id'] == 'pixel-ods'), None)
    if pixel is None or any(hook not in pixel['hooks'] for hook in REQUIRED_MANAGED_HOOKS):
        raise StoreError('invalid-managed-policy')
    lines = []
    for name, value, code in (
        ('OPENCLAW_REQUIRED_PLUGINS', checked, 'invalid-managed-policy'),
        ('PIXEL_ODS_PROVIDER_DEPLOYMENT', doc, 'invalid-managed-deployment'),
    ):
        raw = _encoded(value, code).replace(b'\\', b'\\\\').replace(b'"', b'\\"')
        lines.append(name.encode('ascii') + b'="' + raw + b'"\n')
    return b''.join(lines)
