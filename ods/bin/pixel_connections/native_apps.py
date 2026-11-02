"""Reviewed apps in OpenClaw's native MCP registry, not custom server setup.

Saved entries stay disabled until the custodian completes consent. A registry
entry or credential file is not proof that Portal can use an app. The existing
connection transaction owns every configuration change and its rollback.
"""
from __future__ import annotations

import copy
from typing import Any

NATIVE_MCP_CONTRACTS = {'notion': {'serverName': 'ods-notion', 'url': 'https://mcp.notion.com/mcp',
                                    'tools': ['notion-search', 'notion-fetch']}}
# The coding profile includes bundle-mcp; alsoAllow alone is additive. A new
# agent allowlist keeps core tools and ODS's reviewed plugin families eligible
# under the existing profile/deny layers, without granting all native servers.
# A preexisting explicit agent allowlist remains the owner's restriction.
NATIVE_MCP_BASE_ALLOW = ('group:fs', 'group:runtime', 'group:openclaw', 'pixel-ods',
                       'pixel-source-broker', 'pixel-operations-broker', 'pixel-frontier-broker', 'memory-wiki')
NOTION = {
    'id': 'notion',
    'label': 'Notion',
    'summary': 'Find and read information available through your Notion connection.',
    **copy.deepcopy(NATIVE_MCP_CONTRACTS['notion']),
    'details': [
        'Portal can search and read; this connection does not offer tools that change content.',
        'Notion may return content from other sources connected to your account. Review the access shown by Notion.',
        'Read content enters your conversation and is shared with its selected model provider.',
        'Disconnecting removes Portal access here. Provider-side authorization is managed separately in Notion.',
    ],
}
APPS = (NOTION,)
IDS = tuple(app['id'] for app in APPS)


class NativeAppError(ValueError):
    pass


def app(app_id: str) -> dict[str, Any]:
    for entry in APPS:
        if entry['id'] == app_id:
            return entry
    raise NativeAppError('unknown-native-app')


def public_catalog() -> list[dict[str, Any]]:
    return [{key: copy.deepcopy(entry[key]) for key in ('id', 'label', 'summary', 'tools', 'details')}
            for entry in APPS]


def choices(value: Any) -> dict[str, bool]:
    if (type(value) is not dict or len(value) > len(IDS)
            or any(key not in IDS or type(flag) is not bool for key, flag in value.items())):
        raise NativeAppError('invalid-native-app-choice')
    return {app_id: value[app_id] for app_id in IDS if app_id in value}


def server(app_id: str, enabled: bool) -> dict[str, Any]:
    entry = app(app_id)
    if type(enabled) is not bool:
        raise NativeAppError('invalid-native-app-choice')
    return {'enabled': enabled, 'transport': 'streamable-http', 'auth': 'oauth', 'url': entry['url'],
            'toolFilter': {'include': list(entry['tools'])}}


def tool_names(app_id: str) -> list[str]:
    entry = app(app_id)
    return [entry['serverName'] + '__' + name for name in entry['tools']]


def answers(chosen: dict[str, bool]) -> list[dict[str, Any]] | None:
    chosen = choices(chosen)
    return [{'id': app_id, 'serverName': app(app_id)['serverName'], 'server': server(app_id, enabled),
             'tools': tool_names(app_id)} for app_id, enabled in chosen.items()] or None


def valid_native_app_answers(value):
    """Exact public registry contracts; also used by the standalone overlay."""
    if value is None:
        return True
    if type(value) is not list or not value or len(value) > len(NATIVE_MCP_CONTRACTS):
        return False
    seen = []
    for item in value:
        if type(item) is not dict or set(item) != {'id', 'serverName', 'server', 'tools'}:
            return False
        app_id = item['id']
        if type(app_id) is not str or app_id not in NATIVE_MCP_CONTRACTS or app_id in seen:
            return False
        entry = NATIVE_MCP_CONTRACTS[app_id]
        spec = item['server']
        if type(spec) is not dict or type(spec.get('enabled')) is not bool:
            return False
        expected = {'id': app_id, 'serverName': entry['serverName'],
                    'server': {'enabled': spec['enabled'], 'transport': 'streamable-http', 'auth': 'oauth',
                               'url': entry['url'], 'toolFilter': {'include': list(entry['tools'])}},
                    'tools': [entry['serverName'] + '__' + name for name in entry['tools']]}
        if item != expected:
            return False
        seen.append(app_id)
    return seen == [app_id for app_id in NATIVE_MCP_CONTRACTS if app_id in seen]


valid_answers = valid_native_app_answers


def configured_choices(config: Any) -> dict[str, bool]:
    """Recognize only exact managed shapes; unrelated servers remain opaque.

    A collision is refused by load_native_apps when that app is selected. Merely
    reading the Google/plugin status must not reject someone's custom registry.
    """
    registry = config.get('mcp', {}).get('servers') if type(config) is dict and type(config.get('mcp')) is dict else None
    if type(registry) is not dict:
        return {}
    result = {}
    for entry in APPS:
        current = registry.get(entry['serverName'])
        if type(current) is not dict or type(current.get('enabled')) is not bool:
            continue
        for enabled in (False, True):
            if current == server(entry['id'], enabled):
                result[entry['id']] = enabled
                break
    return result


def load_native_apps(config, chosen):
    """Apply exact reviewed answer entries, preserving unrelated native servers.

    The standalone runtime overlay carries this same transformation; its caller
    validates the answer frame before invoking it. No broad MCP group is granted.
    """
    if not chosen:
        return
    mcp = config.setdefault('mcp', {})
    if type(mcp) is not dict:
        raise ValueError('native MCP registry is not an object')
    registry = mcp.setdefault('servers', {})
    if type(registry) is not dict:
        raise ValueError('native MCP servers are not an object')
    # Exact model-facing grants require server keys that the pinned native
    # sanitizer leaves unchanged. Refuse an ambiguous registry; never rename a
    # custom server or guess which sanitized alias owns the reviewed tool name.
    # Withdrawal remains possible if an unrelated name changed in the meantime.
    if any(entry['server']['enabled'] for entry in chosen) and any(
            type(name) is not str or not 1 <= len(name) <= 30 or name[0] not in 'abcdefghijklmnopqrstuvwxyz'
            or any(char not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for char in name) for name in registry):
        raise ValueError('native MCP server names require review before granting access')
    tools = config['tools']
    agent = next(item for item in config['agents']['list'] if item.get('id') == 'pixel')
    if 'alsoAllow' in agent['tools'] and any(entry['server']['enabled'] for entry in chosen):
        raise ValueError('native MCP agent additive policy requires review')
    reviewed_names = {entry['serverName'] + '__' + name
                      for entry in NATIVE_MCP_CONTRACTS.values() for name in entry['tools']}
    if 'alsoAllow' not in agent['tools'] and any(entry['server']['enabled'] for entry in chosen):
        agent['tools'].setdefault('allow', sorted(set(NATIVE_MCP_BASE_ALLOW) | set(tools['alsoAllow']) | reviewed_names))
    if 'allow' in agent['tools'] and (type(agent['tools']['allow']) is not list
                                    or any(type(name) is not str for name in agent['tools']['allow'])):
        raise ValueError('native MCP agent policy is not a list of names')
    for entry in chosen:
        name, wanted, names = entry['serverName'], entry['server'], entry['tools']
        current = registry.get(name)
        if name in registry and (type(current) is not dict or type(current.get('enabled')) is not bool):
            raise ValueError('managed native MCP server name is already in use')
        if name in registry and current != wanted:
            previous = copy.deepcopy(wanted)
            previous['enabled'] = not wanted['enabled']
            if current != previous:
                raise ValueError('managed native MCP server name is already in use')
        registry[name] = copy.deepcopy(wanted)
        enabled = wanted['enabled']
        for allowed in (tools['alsoAllow'], tools['sandbox']['tools']['allow']):
            allowed[:] = sorted((set(allowed) - set(names)) | (set(names) if enabled else set()))
        denied = agent['tools']['deny']
        denied[:] = [name for name in denied if name not in names]
        if not enabled:
            denied.extend(name for name in names if name not in denied)
