"""Deployment syntax and systemd quoting, not installed activation acceptance."""
import copy
import json
from pathlib import Path
import re
import shlex
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider.managed_deployment import (
    REQUIRED_MANAGED_HOOKS, deployment, environment_bytes, required_policy,
)
from pixel_provider.store import StoreError

BINDING = {'schemaVersion': 1, 'activationId': '123e4567-e89b-12d3-a456-426614174000',
           'revision': 2, 'allowCloud': False}


def make(**changes):
    args = dict(binding=copy.deepcopy(BINDING), source_root='/opt/ods/source',
                host_python='/usr/bin/python3', provider_directory='/home/owner/providers',
                owner_scopes=True)
    args.update(changes)
    return deployment(**args)


def policy(hooks=None, name='other'):
    return {'version': 1, 'plugins': [{'id': name, 'hooks': hooks or ['gateway_start']}]}


def test_complete_hook_union_matches_actual_plugin_without_mutating_input():
    expected = ('before_model_resolve', 'before_agent_run', 'agent_end', 'before_command_run',
                'before_tool_call', 'after_tool_call', 'gateway_stop')
    assert REQUIRED_MANAGED_HOOKS == expected
    source = (Path(__file__).resolve().parents[2] /
              'extensions/services/pixel-agent/plugin/managed-runtime-lifecycle.mjs').read_text()
    block = re.search(r'requiredManagedHooks = Object.freeze\(\[(.*?)\]\)', source, re.S)
    assert tuple(re.findall(r"'([^']+)'", block.group(1))) == expected
    previous = policy()
    previous['plugins'].append({'id': 'pixel-ods', 'hooks': ['gateway_start', 'before_agent_run']})
    before = copy.deepcopy(previous)
    result = required_policy(previous)
    assert previous == before
    assert result['plugins'][0] == before['plugins'][0]
    assert result['plugins'][1]['hooks'] == ['gateway_start', 'before_agent_run'] + [
        hook for hook in expected if hook != 'before_agent_run']
    result['plugins'][0]['hooks'].append('gateway_stop')
    assert previous == before
    assert required_policy()['plugins'] == [{'id': 'pixel-ods', 'hooks': list(expected)}]


@pytest.mark.parametrize('bad', [
    {}, [], {'version': True, 'plugins': []}, {'version': 1, 'plugins': []},
    {'version': 2, 'plugins': []}, dict(policy(), extra='never-echo-secret'),
    {'version': 1, 'plugins': [{'id': 'a', 'hooks': []}]},
    policy(['pre_start']), policy(['gateway_start', 'gateway_start']), policy([{}]),
    policy(name='a b'), policy(name='a\x7f'), policy(name='a\x00'), policy(name=''),
    policy(name='a' * 257), policy(name='\U0001f600' * 129), policy(name='\ud800'),
    {'version': 1, 'plugins': [{'id': 'a', 'hooks': ['gateway_start'], 'extra': 1}]},
    {'version': 1, 'plugins': [{'id': 'a', 'hooks': ['gateway_start']}] * 2},
    {'version': 1, 'plugins': [None]},
])
def test_invalid_policy_refused_without_input_disclosure(bad):
    with pytest.raises(StoreError, match='^invalid-managed-policy$'):
        required_policy(bad)


def test_union_checks_count_and_encoded_byte_limits_after_merge():
    previous = {'version': 1, 'plugins': [
        {'id': str(n), 'hooks': ['gateway_start']} for n in range(64)]}
    with pytest.raises(StoreError):
        required_policy(previous)
    previous['plugins'] = [{'id': '\u00e9' * 230 + str(n), 'hooks': ['gateway_start']}
                           for n in range(12)]
    with pytest.raises(StoreError):
        required_policy(previous)


@pytest.mark.parametrize('change', [
    {'schemaVersion': True}, {'revision': True}, {'revision': -1}, {'revision': 2**53},
    {'revision': 1.0}, {'allowCloud': 0}, {'activationId': 'bad'},
    {'activationId': BINDING['activationId'].upper()}, {'credential': 'never-echo-secret'},
])
def test_strict_binding(change):
    with pytest.raises(StoreError, match='^invalid-managed-deployment$'):
        make(binding=dict(BINDING, **change))


@pytest.mark.parametrize('bad', [None, 'binding', [], {}])
def test_binding_requires_exact_object(bad):
    with pytest.raises(StoreError):
        make(binding=bad)


@pytest.mark.parametrize('path', ['/', '//a', 'relative', '/a/../b', '/a/./b', '/a/', '/a//b',
                                '/a\\b', '/a\x00b', '/a\x7fb', '/' + 'a' * 4096,
                                '/' + '\U0001f600' * 2048, '/\ud800'])
@pytest.mark.parametrize('field', ['source_root', 'host_python', 'provider_directory'])
def test_strict_paths(path, field):
    with pytest.raises(StoreError, match='^invalid-managed-deployment$'):
        make(**{field: path})


@pytest.mark.parametrize('change', [
    {'owner_scopes': []}, {'owner_scopes': 1}, {'lease_timeout_seconds': True},
    {'lease_timeout_seconds': 0}, {'lease_timeout_seconds': 3601},
    {'approval_timeout_seconds': False}, {'approval_timeout_seconds': 0},
    {'approval_timeout_seconds': 121}, {'lease_timeout_seconds': 60},
])
def test_strict_timing_and_scope(change):
    with pytest.raises(StoreError, match='^invalid-managed-deployment$'):
        make(**change)


def test_deployment_size_limit_and_detached_binding():
    original = copy.deepcopy(BINDING)
    result = make(binding=original)
    result['binding']['revision'] = 5
    assert original == BINDING
    with pytest.raises(StoreError):
        make(source_root='/' + '\u00e9' * 3000)


@pytest.mark.parametrize('change', [
    {'leaseTimeoutSeconds': 0}, {'leaseTimeoutSeconds': 60}, {'approvalTimeoutSeconds': 121},
    {'ownerScopes': []}, {'sourceRoot': '/' + '\u00e9' * 3000}, {'extra': 'never-echo-secret'},
])
def test_environment_revalidates_deployment(change):
    with pytest.raises(StoreError, match='^invalid-managed-deployment$'):
        environment_bytes(dict(make(), **change), required_policy())


def test_environment_requires_every_hook_without_silent_repair():
    for hook in REQUIRED_MANAGED_HOOKS:
        previous = required_policy()
        previous['plugins'][0]['hooks'].remove(hook)
        with pytest.raises(StoreError, match='^invalid-managed-policy$'):
            environment_bytes(make(), previous)
    with pytest.raises(StoreError):
        environment_bytes(make(), policy())


def test_quote_sensitive_values_roundtrip_as_only_two_variables():
    doc = make(source_root='/opt/owner\'s "$HOME" `id` \u00e9',
               provider_directory='/home/owner/\U0001f600 providers')
    previous = required_policy(policy(name='other"$`\\plugin'))
    before = copy.deepcopy((doc, previous))
    raw = environment_bytes(doc, previous)
    assert raw.endswith(b'\n') and len(raw.splitlines()) == 2
    # Unit parser only; real systemd qualification is a separate integration run.
    parsed = {}
    for line in raw.decode('ascii').splitlines():
        name, value = shlex.split(line, posix=True)[0].split('=', 1)
        parsed[name] = json.loads(value)
    assert parsed == {'OPENCLAW_REQUIRED_PLUGINS': previous, 'PIXEL_ODS_PROVIDER_DEPLOYMENT': doc}
    assert (doc, previous) == before
