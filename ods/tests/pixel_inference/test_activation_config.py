"""Managed config projection is reversible; it is not activation authority."""
import copy
import json
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider.activation_config import plan_activation, restore_activation, update_activation
from pixel_provider.store import StoreError


@pytest.fixture
def config():
    return {'agents': {'defaults': {'model': {'primary': 'legacy/default'}, 'workspace': '/unchanged'},
            'list': [{'id': 'pixel', 'model': 'legacy/selected', 'sandbox': {'mode': 'all'}},
                     {'id': 'other', 'model': 'legacy/other'}]},
            'models': {'mode': 'merge', 'providers': {'legacy': {'apiKey': 'never-echo-secret', 'models': []}}},
            'tools': {'deny': ['exec']},
            'plugins': {'entries': {'pixel-ods': {'enabled': True, 'hooks': {'allowConversationAccess': True},
                                                 'config': {'leanPrompt': True}}}},
            'other': {'preserve': ['every', 'field']}}


def make(config, **changes):
    args = {'revision': 4, 'allow_cloud': False, 'activation_id': str(uuid.uuid4())}
    args.update(changes)
    return plan_activation(config, **args)


def test_scoped_projection_and_exact_restore_do_not_mutate_inputs(config):
    original = copy.deepcopy(config)
    plan = make(config)
    candidate = plan['document']
    assert config == original
    assert candidate['agents']['defaults'] == config['agents']['defaults']
    assert candidate['agents']['list'][1] == config['agents']['list'][1]
    assert candidate['tools'] == config['tools']
    assert candidate['models']['providers']['legacy'] == config['models']['providers']['legacy']
    assert candidate['agents']['list'][0]['model'] == {'primary': 'ods-policy/managed', 'fallbacks': []}
    assert candidate['models']['providers']['ods-policy']['baseUrl'] == 'http://127.0.0.1:1/v1'
    assert len(plan['fields']) == 3
    assert 'never-echo-secret' not in json.dumps(plan['fields'])
    prior_plan = copy.deepcopy(plan)
    assert restore_activation(candidate, plan) == original
    assert plan == prior_plan


@pytest.mark.parametrize('missing', ['models', 'providers', 'plugin-config', 'pixel-model'])
def test_missing_parent_presence_is_restored(config, missing):
    if missing == 'models': del config['models']
    elif missing == 'providers': del config['models']['providers']
    elif missing == 'plugin-config': del config['plugins']['entries']['pixel-ods']['config']
    else: del config['agents']['list'][0]['model']
    plan = make(config)
    assert restore_activation(plan['document'], plan) == config


def test_restore_preserves_unrelated_changes_and_new_parent_contents(config):
    del config['models']
    del config['plugins']['entries']['pixel-ods']['config']
    plan = make(config)
    current = copy.deepcopy(plan['document'])
    current['models']['providers']['new'] = {'models': []}
    current['plugins']['entries']['pixel-ods']['config']['leanPrompt'] = False
    current['agents']['list'][1]['name'] = 'Owner edited another agent'
    restored = restore_activation(current, plan)
    assert restored['models'] == {'providers': {'new': {'models': []}}}
    assert restored['plugins']['entries']['pixel-ods']['config'] == {'leanPrompt': False}
    assert restored['agents']['list'][1]['name'] == 'Owner edited another agent'
    assert restored['agents']['list'][0]['model'] == 'legacy/selected'


@pytest.mark.parametrize('leaf', ['model', 'provider', 'binding'])
def test_restore_refuses_drift_without_echoing_config(config, leaf):
    plan = make(config); current = copy.deepcopy(plan['document'])
    if leaf == 'model': current['agents']['list'][0]['model'] = 'never-echo-secret'
    elif leaf == 'provider': current['models']['providers']['ods-policy']['apiKey'] = 'never-echo-secret'
    else: current['plugins']['entries']['pixel-ods']['config']['managedProvider']['revision'] += 1
    snapshot = copy.deepcopy(current)
    with pytest.raises(StoreError) as error: restore_activation(current, plan)
    assert 'never-echo-secret' not in str(error.value)
    assert current == snapshot


@pytest.mark.parametrize('change', [dict(revision=True), dict(revision=-1), dict(revision=2**53),
                                  dict(revision=1.0), dict(allow_cloud=1), dict(activation_id='bad')])
def test_strict_binding(config, change):
    with pytest.raises(StoreError): make(config, **change)


@pytest.mark.parametrize('scope', ['global', 'agent'])
def test_provider_specific_tool_policy_is_not_silently_widened(config, scope):
    target = config if scope == 'global' else config['agents']['list'][0]
    target['tools'] = {'byProvider': {'legacy': {'deny': ['exec']}}}
    with pytest.raises(StoreError, match='unsupported-provider-tool-policy'): make(config)


@pytest.mark.parametrize('kind', ['provider', 'binding', 'hooks', 'plugin', 'duplicate-agent', 'malformed-parent'])
def test_collision_and_unqualified_config_refused(config, kind):
    entry = config['plugins']['entries']['pixel-ods']
    if kind == 'provider': config['models']['providers']['ods-policy'] = {}
    elif kind == 'binding': entry['config']['managedProvider'] = {}
    elif kind == 'hooks': entry['hooks']['allowConversationAccess'] = False
    elif kind == 'plugin': entry['enabled'] = False
    elif kind == 'duplicate-agent': config['agents']['list'].append({'id': 'pixel'})
    else: config['models'] = 'never-echo-secret'
    with pytest.raises(StoreError) as error: make(config)
    assert 'never-echo-secret' not in str(error.value)


def test_restore_rejects_malformed_private_plan(config):
    plan = make(config)
    for key, value in [('schemaVersion', True), ('fields', {}), ('parents', {'bad': True})]:
        bad = copy.deepcopy(plan); bad[key] = value
        with pytest.raises(StoreError): restore_activation(plan['document'], bad)


def test_replace_mode_and_explicit_null_are_not_coerced(config):
    config['models']['mode'] = 'replace'
    config['agents']['list'][0]['model'] = None
    plan = make(config)
    assert plan['document']['models']['mode'] == 'replace'
    assert restore_activation(plan['document'], plan) == config


@pytest.mark.parametrize('value', [float('nan'), float('inf'), (1, 2), {1: 'non-string-key'}])
def test_non_json_config_rejected_without_mutation(config, value):
    config['unrelated'] = value
    with pytest.raises(StoreError, match='invalid-activation-json'): make(config)


def test_cyclic_config_is_rejected_with_content_free_error(config):
    config['cycle'] = config
    with pytest.raises(StoreError, match='invalid-activation-json'): make(config)


@pytest.mark.parametrize('path', ['models', 'providers', 'pluginConfig', 'tools', 'byProvider'])
@pytest.mark.parametrize('value', [None, [], 'never-echo-secret'])
def test_malformed_parents_not_silently_replaced(config, path, value):
    if path == 'models': config['models'] = value
    elif path == 'providers': config['models']['providers'] = value
    elif path == 'pluginConfig': config['plugins']['entries']['pixel-ods']['config'] = value
    elif path == 'tools': config['tools'] = value
    else: config['tools']['byProvider'] = value
    original = copy.deepcopy(config)
    with pytest.raises(StoreError) as error: make(config)
    assert 'never-echo-secret' not in str(error.value)
    assert original == config


def test_restore_matches_agent_identity_and_preserves_disabled_plugin(config):
    plan = make(config)
    current = copy.deepcopy(plan['document'])
    current['agents']['list'].reverse()
    current['plugins']['entries']['pixel-ods']['enabled'] = False
    current['tools']['byProvider'] = {'legacy': {'deny': ['exec']}}
    restored = restore_activation(current, plan)
    assert restored['agents']['list'][1]['model'] == 'legacy/selected'
    assert restored['agents']['list'][0]['id'] == 'other'
    assert restored['plugins']['entries']['pixel-ods']['enabled'] is False
    assert restored['tools'] == current['tools']


@pytest.mark.parametrize('value', [True, 1.0])
def test_restore_rejects_numerically_equal_but_different_types(config, value):
    plan = make(config, revision=1)
    current = copy.deepcopy(plan['document'])
    current['plugins']['entries']['pixel-ods']['config']['managedProvider']['revision'] = value
    with pytest.raises(StoreError): restore_activation(current, plan)


@pytest.mark.parametrize('parent', ['models', 'providers', 'pluginConfig'])
def test_preexisting_empty_parents_are_preserved(config, parent):
    if parent == 'models': config['models'] = {}
    elif parent == 'providers': config['models']['providers'] = {}
    else: config['plugins']['entries']['pixel-ods']['config'] = {}
    plan = make(config)
    assert restore_activation(plan['document'], plan) == config


@pytest.mark.parametrize('tamper', ['unknown-field', 'missing-field', 'false-presence', 'parent-type',
                                  'parent-order', 'present-provider', 'wrong-after', 'binding-version',
                                  'preview-drift', 'extra-plan-key'])
def test_private_plan_schema_and_fixed_projection_are_validated(config, tamper):
    plan = make(config); current = copy.deepcopy(plan['document'])
    if tamper == 'unknown-field': plan['fields']['arbitrary.path'] = plan['fields']['model']
    elif tamper == 'missing-field': del plan['fields']['model']
    elif tamper == 'false-presence': plan['fields']['model']['present'] = False
    elif tamper == 'parent-type': plan['parents']['models'] = 1
    elif tamper == 'parent-order': plan['parents']['models'] = False
    elif tamper == 'present-provider': plan['fields']['provider']['present'] = True
    elif tamper == 'wrong-after': plan['fields']['model']['after'] = 'legacy/selected'
    elif tamper == 'binding-version': plan['fields']['binding']['after']['schemaVersion'] = True
    elif tamper == 'preview-drift': del plan['document']['models']['providers']['ods-policy']
    else: plan['unexpected'] = None
    snapshot = copy.deepcopy(current)
    with pytest.raises(StoreError): restore_activation(current, plan)
    assert current == snapshot


def test_result_has_no_mutable_aliases_to_config_plan_or_module_constants(config):
    plan = make(config)
    restored = restore_activation(plan['document'], plan)
    restored['other']['preserve'].append('edit')
    assert config['other']['preserve'] == ['every', 'field']
    plan['document']['models']['providers']['ods-policy']['models'][0]['input'].append('image')
    assert plan['fields']['provider']['after']['models'][0]['input'] == ['text']
    assert make(config)['document']['models']['providers']['ods-policy']['models'][0]['input'] == ['text']


@pytest.mark.parametrize('kind', ['agents', 'missing-agent', 'duplicate-agent', 'models', 'plugin'])
def test_restore_rejects_structural_corruption_before_mutation(config, kind):
    plan = make(config); current = copy.deepcopy(plan['document'])
    if kind == 'agents': current['agents'] = None
    elif kind == 'missing-agent': current['agents']['list'] = [{'id': 'other'}]
    elif kind == 'duplicate-agent': current['agents']['list'].append({'id': 'pixel'})
    elif kind == 'models': current['models'] = 'never-echo-secret'
    else: current['plugins']['entries']['pixel-ods'] = []
    original = copy.deepcopy(current)
    with pytest.raises(StoreError) as error: restore_activation(current, plan)
    assert 'never-echo-secret' not in str(error.value)
    assert current == original


def update(current, plan, **changes):
    args = dict(revision=plan['fields']['binding']['after']['revision'] + 1,
                allow_cloud=False, activation_id=str(uuid.uuid4()))
    args.update(changes)
    return update_activation(current, plan, **args)


def test_update_retains_original_route_through_multiple_revisions(config):
    original = copy.deepcopy(config)
    plan = make(config)
    for revision in range(5, 9):
        previous = copy.deepcopy(plan)
        current = copy.deepcopy(plan['document'])
        next_plan = update(current, plan, revision=revision, allow_cloud=revision % 2 == 0)
        assert current == previous['document'] and plan == previous
        assert next_plan['fields']['model']['before'] == 'legacy/selected'
        assert next_plan['fields']['binding']['after']['revision'] == revision
        assert next_plan['fields']['binding']['after']['activationId'] != previous['fields']['binding']['after']['activationId']
        assert restore_activation(next_plan['document'], next_plan) == original
        plan = next_plan
    assert config == original


@pytest.mark.parametrize('missing', ['models', 'providers', 'plugin-config', 'pixel-model'])
def test_update_preserves_original_absence_and_unrelated_owner_edits(config, missing):
    if missing == 'models': del config['models']
    elif missing == 'providers': del config['models']['providers']
    elif missing == 'plugin-config': del config['plugins']['entries']['pixel-ods']['config']
    else: del config['agents']['list'][0]['model']
    plan = make(config)
    current = copy.deepcopy(plan['document'])
    current['agents']['list'].reverse()
    current['agents']['list'][0]['name'] = 'Unrelated owner edit'
    current['models']['providers']['added-by-owner'] = {'models': []}
    expected = restore_activation(current, plan)
    next_plan = update(current, plan)
    assert restore_activation(next_plan['document'], next_plan) == expected
    assert 'ods-policy' not in expected.get('models', {}).get('providers', {})


@pytest.mark.parametrize('revision', [True, 5.0, -1, 3, 4, 2**53])
def test_update_requires_a_strictly_newer_valid_revision(config, revision):
    plan = make(config); before = copy.deepcopy(plan)
    with pytest.raises(StoreError): update(plan['document'], plan, revision=revision)
    assert plan == before


def test_update_refuses_reusing_activation_identity(config):
    plan = make(config)
    with pytest.raises(StoreError, match='activation-id-reused'):
        update(plan['document'], plan, activation_id=plan['fields']['binding']['after']['activationId'])


@pytest.mark.parametrize('leaf', ['model', 'provider', 'binding'])
def test_update_refuses_managed_drift_without_mutation(config, leaf):
    plan = make(config); current = copy.deepcopy(plan['document'])
    if leaf == 'model': current['agents']['list'][0]['model'] = 'never-echo-secret'
    elif leaf == 'provider': current['models']['providers']['ods-policy']['apiKey'] = 'never-echo-secret'
    else: current['plugins']['entries']['pixel-ods']['config']['managedProvider']['revision'] = 99
    snapshot = copy.deepcopy(current)
    with pytest.raises(StoreError) as error: update(current, plan)
    assert current == snapshot and 'never-echo-secret' not in str(error.value)


@pytest.mark.parametrize('kind', ['disabled', 'conversation-denied', 'global-tools', 'pixel-tools'])
def test_update_rechecks_current_activation_eligibility(config, kind):
    plan = make(config); current = copy.deepcopy(plan['document'])
    if kind == 'disabled': current['plugins']['entries']['pixel-ods']['enabled'] = False
    elif kind == 'conversation-denied': current['plugins']['entries']['pixel-ods']['hooks']['allowConversationAccess'] = False
    else:
        target = current if kind == 'global-tools' else current['agents']['list'][0]
        target['tools'] = {'byProvider': {'legacy': {'deny': ['exec']}}}
    snapshot = copy.deepcopy(current)
    with pytest.raises(StoreError): update(current, plan)
    assert current == snapshot
