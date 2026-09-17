"""Contract tests for bin/pixel_model_contract.py.

The model contract is the fixed metadata projection Pixel applies to the
managed OpenClaw route: which model id is bound, the context/output budget the
generation path advertises, and the compaction reserves derived from them.
`pixel_model_coordinator.py` and the installer/host-install libs consume these
functions; there is no endpoint, file or execution authority here.
"""
import copy
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from pixel_model_contract import ModelError, binding, checksum, plan, projection, target


def make_target(**changes):
    value = dict(model='qwen3-4b-instruct', contextLength=32768, maxTokens=4096, reasoning=False)
    value.update(changes)
    return value


def local_config(model_id='qwen3-4b-instruct', *, dict_model=False, **agent_overrides):
    agent = dict(id='pixel', model={'primary': 'ods-local/' + model_id} if dict_model
                 else 'ods-local/' + model_id, contextTokens=32768)
    agent.update(agent_overrides)
    return {
        'agents': {'list': [agent], 'defaults': {}},
        'models': {'providers': {
            'ods-local': {'models': [dict(id=model_id, name='ODS Local ' + model_id,
                                          contextWindow=32768, maxTokens=4096, reasoning=False)]},
            'ods-gateway': {'models': [dict(id='ods/current', name='ODS Current (remote-70b)',
                                          contextWindow=131072, maxTokens=8192, reasoning=True)]}}},
        'plugins': {'entries': {'pixel-ods': {'enabled': True, 'config': {}}}},
    }


def gateway_config():
    config = local_config()
    config['agents']['list'][0]['model'] = 'ods-gateway/ods/current'
    config['plugins']['entries']['pixel-ods']['config']['modelRouteFingerprint'] = 'a' * 64
    return config


class TestChecksum:
    @pytest.mark.parametrize('value', ['a' * 64, '0123456789abcdef' * 4])
    def test_accepts_lower_hex(self, value):
        assert checksum(value) is True

    @pytest.mark.parametrize('value', ['A' * 64, 'g' * 64, 'a' * 63, 'a' * 65, 64, None, ''])
    def test_rejects_everything_else(self, value):
        assert checksum(value) is False


class TestTarget:
    def test_accepts_and_copies_a_valid_contract(self):
        proposed = make_target(routeFingerprint='b' * 64)
        result = target(proposed)
        assert result == proposed and result is not proposed

    @pytest.mark.parametrize('patch', [
        {'model': ''}, {'model': 'bad\nname'}, {'model': '-leading'},
        {'model': 'x' * 257}, {'model': 7},
        {'contextLength': 4095}, {'contextLength': 10_000_001}, {'contextLength': '32768'},
        {'contextLength': 32768.5}, {'maxTokens': 0}, {'maxTokens': -1},
        {'maxTokens': 40000}, {'reasoning': 'yes'}, {'reasoning': 1},
        {'routeFingerprint': 'nope'},
    ])
    def test_rejects_out_of_contract_values(self, patch):
        with pytest.raises(ModelError, match='invalid-model-contract'):
            target(make_target(**patch))

    @pytest.mark.parametrize('patch', [{'extra': 1}, {'model': None}])
    def test_rejects_missing_and_unknown_keys(self, patch):
        value = make_target()
        value.pop('model', None)
        value.update(patch)
        with pytest.raises(ModelError, match='invalid-model-contract'):
            target(value)


class TestBinding:
    def test_extracts_local_route(self):
        agent, row, settings, provider, name = binding(local_config())
        assert (provider, name) == ('ods-local', 'qwen3-4b-instruct')
        assert row['name'] == 'ODS Local qwen3-4b-instruct'

    def test_extracts_gateway_display_name(self):
        agent, row, settings, provider, name = binding(gateway_config())
        assert (provider, name) == ('ods-gateway', 'remote-70b')

    def test_accepts_dict_selected_model(self):
        agent, row, settings, provider, name = binding(local_config(dict_model=True))
        assert name == 'qwen3-4b-instruct'

    @pytest.mark.parametrize('mutate', [
        lambda c: c['agents'].update(list=[]),
        lambda c: c['agents']['list'].append(dict(c['agents']['list'][0])),
        lambda c: c['agents']['list'][0].update(id='other'),
        lambda c: c['agents']['list'][0].update(model='other/qwen3-4b-instruct'),
        lambda c: c['agents']['list'][0].update(model='ods-local/missing'),
        lambda c: c['models']['providers']['ods-local']['models'].append(
            dict(id='qwen3-4b-instruct', name='ODS Local qwen3-4b-instruct')),
        lambda c: c['models']['providers']['ods-local']['models'][0].update(name='Wrong Name'),
        lambda c: c['plugins']['entries']['pixel-ods'].update(enabled=False),
        lambda c: c['plugins']['entries']['pixel-ods'].setdefault('config', {}).update(managedProvider='x'),
        lambda c: c['plugins']['entries'].pop('pixel-ods'),
    ])
    def test_rejects_unmanaged_routes(self, mutate):
        config = local_config()
        mutate(config)
        with pytest.raises(ModelError, match='model-route-not-managed'):
            binding(config)

    @pytest.mark.parametrize('mutate', [
        lambda c: c['models']['providers']['ods-gateway']['models'][0].update(name='ODS Current remote-70b'),
        lambda c: c['models']['providers']['ods-gateway']['models'][0].update(id='default'),
        lambda c: c['agents']['list'][0].update(model='ods-gateway/unknown'),
    ])
    def test_gateway_label_must_wrap_the_bound_model(self, mutate):
        config = gateway_config()
        mutate(config)
        with pytest.raises(ModelError, match='model-route-not-managed'):
            binding(config)


class TestProjection:
    def test_projects_contract_and_limits(self):
        result = projection(local_config())
        assert result['contract'] == make_target()
        assert result['limits'] == {'contextTokens': 32768, 'maxOutputTokens': 4096,
                                    'pluginContext': 32768, 'reserveTokens': None,
                                    'reserveTokensFloor': None, 'keepRecentTokens': None}

    def test_agent_context_tokens_override_defaults(self):
        config = local_config(contextTokens=65536)
        config['agents']['defaults']['contextTokens'] = 131072
        assert projection(config)['limits']['contextTokens'] == 65536

    def test_params_layers_resolve_output_budget_in_order(self):
        config = local_config()
        config['agents']['defaults']['params'] = {'maxTokens': 1000}
        config['agents']['defaults']['models'] = {'ods-local/qwen3-4b-instruct': {'params': {'max_tokens': 2000}}}
        config['agents']['list'][0]['params'] = {'max_completion_tokens': 3000}
        assert projection(config)['limits']['maxOutputTokens'] == 3000

    def test_invalid_alias_falls_through_to_next_alias_in_layer(self):
        config = local_config()
        config['agents']['list'][0]['params'] = {'maxTokens': -5, 'max_completion_tokens': 1536}
        assert projection(config)['limits']['maxOutputTokens'] == 1536

    def test_declared_but_never_valid_params_are_rejected(self):
        config = local_config()
        config['agents']['defaults']['params'] = {'maxTokens': 'lots'}
        config['agents']['list'][0]['params'] = {'max_tokens': -1}
        with pytest.raises(ModelError, match='invalid-model-limits'):
            projection(config)

    def test_gateway_route_fingerprint_requires_gateway_provider(self):
        result = projection(gateway_config())
        assert result['contract']['routeFingerprint'] == 'a' * 64
        config = local_config()
        config['plugins']['entries']['pixel-ods']['config']['modelRouteFingerprint'] = 'a' * 64
        with pytest.raises(ModelError, match='model-route-not-managed'):
            projection(config)

    @pytest.mark.parametrize('patch', [
        {'contextTokens': 0}, {'contextTokens': '32768'}, {'contextTokens': 10_000_001},
    ])
    def test_limit_bounds_enforced(self, patch):
        with pytest.raises(ModelError, match='invalid-model-limits'):
            projection(local_config(**patch))

    def test_compaction_reserves_may_be_absent_but_bounded_when_present(self):
        config = local_config()
        config['agents']['defaults']['compaction'] = {'reserveTokens': 5000, 'keepRecentTokens': 0}
        limits = projection(config)['limits']
        assert limits['reserveTokens'] == 5000 and limits['keepRecentTokens'] == 0

    @pytest.mark.parametrize('compaction', [
        {'reserveTokens': -1}, {'reserveTokens': 10_000_001}, {'keepRecentTokens': 'all'},
        {'reserveTokensFloor': 1.5},
    ])
    def test_compaction_reserves_reject_out_of_range_values(self, compaction):
        config = local_config()
        config['agents']['defaults']['compaction'] = compaction
        with pytest.raises(ModelError, match='invalid-model-limits'):
            projection(config)


class TestPlan:
    def test_local_plan_rewrites_id_name_budget_and_compaction(self):
        proposed = make_target(model='new-model-8b', contextLength=65536, maxTokens=8192,
                               reasoning=True)
        result = plan(local_config(), proposed)
        row = result['models']['providers']['ods-local']['models'][0]
        assert row['id'] == 'new-model-8b' and row['name'] == 'ODS Local new-model-8b'
        assert row['contextWindow'] == 65536 and row['maxTokens'] == 8192 and row['reasoning'] is True
        agent = result['agents']['list'][0]
        assert agent['model'] == 'ods-local/new-model-8b' and agent['contextTokens'] == 65536
        assert agent['params'] == {'maxTokens': 8192}
        assert agent['contextLimits']['toolResultMaxChars'] == 16000
        compaction = result['agents']['defaults']['compaction']
        assert compaction['reserveTokens'] == (65536 + 4 * 8192 + 4) // 5
        assert compaction['reserveTokensFloor'] == 0
        assert 512 <= compaction['keepRecentTokens'] <= 20000
        settings = result['plugins']['entries']['pixel-ods']['config']
        assert settings['modelContextWindow'] == 65536 and 'modelRouteFingerprint' not in settings
        assert projection(result)['contract'] == proposed

    def test_plan_rewrites_dict_primary_selection(self):
        result = plan(local_config(dict_model=True), make_target(model='swapped-2b'))
        assert result['agents']['list'][0]['model'] == {'primary': 'ods-local/swapped-2b'}

    def test_plan_rejects_dict_selection_with_fallbacks(self):
        config = local_config(dict_model=True)
        config['agents']['list'][0]['model']['fallbacks'] = ['ods-local/other']
        with pytest.raises(ModelError, match='model-route-not-managed'):
            plan(config, make_target())

    def test_plan_rejects_route_fingerprint_on_local(self):
        with pytest.raises(ModelError, match='model-route-not-managed'):
            plan(local_config(), make_target(routeFingerprint='c' * 64))

    def test_gateway_plan_wraps_name_and_keeps_fingerprint(self):
        proposed = make_target(model='remote-70b-v2', contextLength=131072, maxTokens=8192,
                               reasoning=True, routeFingerprint='d' * 64)
        result = plan(gateway_config(), proposed)
        row = result['models']['providers']['ods-gateway']['models'][0]
        assert row['name'] == 'ODS Current (remote-70b-v2)'
        assert result['plugins']['entries']['pixel-ods']['config']['modelRouteFingerprint'] == 'd' * 64
        assert projection(result)['contract'] == proposed

    def test_plan_drops_conflicting_output_aliases(self):
        config = local_config()
        config['agents']['list'][0]['params'] = {'max_tokens': 111, 'max_completion_tokens': 222}
        result = plan(config, make_target(maxTokens=7777))
        assert result['agents']['list'][0]['params'] == {'maxTokens': 7777}

    def test_lean_bootstrap_for_small_contexts_and_small_models(self):
        lean = plan(local_config(), make_target(contextLength=16384, maxTokens=2048))
        agent = lean['agents']['list'][0]
        assert agent['bootstrapMaxChars'] == 2000 and agent['bootstrapTotalMaxChars'] == 6000
        assert agent['contextInjection'] == 'never'
        named = plan(local_config(), make_target(model='some-3b-model'))
        assert named['agents']['list'][0]['bootstrapMaxChars'] == 2000
        large = plan(local_config(), make_target(model='some-70b-model'))
        assert large['agents']['list'][0]['bootstrapMaxChars'] == 14000
        assert large['agents']['list'][0]['contextInjection'] == 'continuation-skip'

    def test_plan_verifies_its_own_projection(self):
        result = plan(local_config(), make_target())
        assert projection(result)['contract'] == make_target()

    def test_plan_does_not_mutate_the_input(self):
        config = local_config()
        before = copy.deepcopy(config)
        plan(config, make_target(model='changed-1b'))
        assert config == before
