"""Per-call eligibility matrix for runtime_policy.select_candidates.

This is the decision point before every routed generation: which provider
order is legal for this payload, given tools/vision/reasoning requirements,
output budget, cloud authorization, and route-cycle prevention.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'bin'))
from pixel_provider.runtime_policy import select_candidates  # noqa: E402
from pixel_provider.store import StoreError  # noqa: E402


def provider(pid, **kw):
    doc = {'id': pid, 'label': pid, 'kind': 'local', 'baseUrl': 'http://x',
           'model': 'm/' + pid, 'enabled': True, 'contextTokens': 32768,
           'maxOutputTokens': 4096, 'supportsTools': True,
           'supportsVision': True, 'reasoning': False}
    doc.update(kw)
    return doc


def config(**kw):
    doc = {'enabled': True, 'revision': 1,
           'providers': [provider('a'), provider('b'), provider('c')],
           'roles': {'leader': 'a', 'backups': ['b', 'c'],
                     'advisor': None, 'handoff': None},
           'policy': {'allowCloud': False, 'maxAttempts': 3,
                      'deadlineSeconds': 30}}
    doc.update(kw)
    return doc


def payload(**kw):
    doc = {'messages': [{'role': 'user', 'content': 'hi'}]}
    doc.update(kw)
    return doc


class TestGates:
    def test_routing_disabled(self):
        with pytest.raises(StoreError, match='provider-routing-disabled'):
            select_candidates(config(enabled=False), payload())

    def test_cloud_not_authorized(self):
        cfg = config()
        cfg['providers'][0]['kind'] = 'cloud'
        with pytest.raises(StoreError, match='cloud-not-authorized'):
            select_candidates(cfg, payload())

    def test_cloud_allowed_when_policy_permits(self):
        cfg = config()
        cfg['providers'][0]['kind'] = 'cloud'
        cfg['policy']['allowCloud'] = True
        selected, _ = select_candidates(cfg, payload())
        assert selected[0]['id'] == 'a'

    def test_cloud_backup_rejected_without_policy(self):
        cfg = config()
        cfg['providers'][1]['kind'] = 'cloud'
        with pytest.raises(StoreError, match='cloud-not-authorized'):
            select_candidates(cfg, payload())

    @pytest.mark.parametrize('model', ['ods/pixel', 'pixel/default',
                                       'openclaw/default'])
    def test_route_cycle_model_rejected(self, model):
        cfg = config()
        cfg['providers'][0]['model'] = model
        with pytest.raises(StoreError, match='provider-route-cycle'):
            select_candidates(cfg, payload())

    def test_route_cycle_in_backup_rejected(self):
        cfg = config()
        cfg['providers'][2]['model'] = 'ods/pixel'
        with pytest.raises(StoreError, match='provider-route-cycle'):
            select_candidates(cfg, payload())


class TestBudget:
    def test_default_budget_min_1024(self):
        cfg = config()
        cfg['providers'][0]['maxOutputTokens'] = 512
        selected, skipped = select_candidates(cfg, payload())
        # leader a (512) is leader — budget defaults to min(1024, 512)=512, ok
        assert selected[0]['id'] == 'a'

    def test_max_tokens_over_leader_limit(self):
        with pytest.raises(StoreError, match='output-limit-exceeded'):
            select_candidates(config(), payload(max_tokens=4097))

    def test_max_completion_tokens_alternative(self):
        selected, _ = select_candidates(
            config(), payload(max_completion_tokens=2048))
        assert selected[0]['id'] == 'a'

    @pytest.mark.parametrize('bad', [0, -1, 'x', True, 4097, 1.5])
    def test_invalid_budget(self, bad):
        with pytest.raises(StoreError, match='output-limit-exceeded'):
            select_candidates(config(), payload(max_tokens=bad))

    def test_budget_backup_skipped_below_budget(self):
        cfg = config()
        cfg['providers'][1]['maxOutputTokens'] = 2048
        selected, skipped = select_candidates(cfg, payload(max_tokens=3000))
        assert [s['id'] for s in selected] == ['a', 'c']
        assert skipped == [{'providerId': 'b', 'reason': 'incompatible-backup'}]


class TestCapabilityMatrix:
    def test_tools_flag(self):
        cfg = config()
        cfg['providers'][1]['supportsTools'] = False
        selected, skipped = select_candidates(
            cfg, payload(tools=[{'name': 't'}]))
        assert [s['id'] for s in selected] == ['a', 'c']
        assert skipped[0]['providerId'] == 'b'

    def test_tools_via_message_role(self):
        cfg = config()
        cfg['providers'][1]['supportsTools'] = False
        p = payload()
        p['messages'].append({'role': 'tool', 'content': 'x'})
        selected, skipped = select_candidates(cfg, p)
        assert [s['id'] for s in selected] == ['a', 'c']

    def test_tools_via_tool_calls(self):
        cfg = config()
        cfg['providers'][1]['supportsTools'] = False
        p = payload()
        p['messages'][0]['tool_calls'] = [{'id': '1'}]
        selected, _ = select_candidates(cfg, p)
        assert [s['id'] for s in selected] == ['a', 'c']

    def test_no_tools_allows_no_support(self):
        cfg = config()
        cfg['providers'][1]['supportsTools'] = False
        selected, _ = select_candidates(cfg, payload())
        assert [s['id'] for s in selected] == ['a', 'b', 'c']

    def test_vision_flag(self):
        cfg = config()
        cfg['providers'][1]['supportsVision'] = False
        p = payload()
        p['messages'][0]['content'] = [{'type': 'image_url',
                                      'image_url': {'url': 'x'}}]
        selected, skipped = select_candidates(cfg, p)
        assert [s['id'] for s in selected] == ['a', 'c']

    def test_text_list_content_not_vision(self):
        cfg = config()
        cfg['providers'][1]['supportsVision'] = False
        p = payload()
        p['messages'][0]['content'] = [{'type': 'text', 'text': 'x'}]
        selected, _ = select_candidates(cfg, p)
        assert [s['id'] for s in selected] == ['a', 'b', 'c']

    def test_reasoning_leader_requires_reasoning_backups(self):
        cfg = config()
        cfg['providers'][0]['reasoning'] = True
        cfg['providers'][1]['reasoning'] = True
        cfg['providers'][2]['reasoning'] = False
        selected, skipped = select_candidates(cfg, payload())
        assert [s['id'] for s in selected] == ['a', 'b']
        assert skipped[0]['providerId'] == 'c'

    def test_non_reasoning_leader_allows_all(self):
        cfg = config()
        cfg['providers'][1]['reasoning'] = True
        selected, _ = select_candidates(cfg, payload())
        assert [s['id'] for s in selected] == ['a', 'b', 'c']


class TestCompatibility:
    def test_leader_disabled_is_fatal(self):
        cfg = config()
        cfg['providers'][0]['enabled'] = False
        with pytest.raises(StoreError, match='leader-incompatible'):
            select_candidates(cfg, payload())

    def test_backup_disabled_skipped(self):
        cfg = config()
        cfg['providers'][1]['enabled'] = False
        selected, skipped = select_candidates(cfg, payload())
        assert [s['id'] for s in selected] == ['a', 'c']
        assert skipped[0]['providerId'] == 'b'

    def test_backup_smaller_context_skipped(self):
        cfg = config()
        cfg['providers'][1]['contextTokens'] = 16384
        selected, skipped = select_candidates(cfg, payload())
        assert [s['id'] for s in selected] == ['a', 'c']

    def test_backup_equal_context_ok(self):
        cfg = config()
        cfg['providers'][1]['contextTokens'] = 32768
        selected, _ = select_candidates(cfg, payload())
        assert [s['id'] for s in selected] == ['a', 'b', 'c']

    def test_leader_incompatible_via_tools(self):
        cfg = config()
        cfg['providers'][0]['supportsTools'] = False
        with pytest.raises(StoreError, match='leader-incompatible'):
            select_candidates(cfg, payload(tools=[{'name': 't'}]))


class TestOrdering:
    def test_leader_first_then_backup_order(self):
        cfg = config()
        cfg['roles']['backups'] = ['c', 'b']
        selected, _ = select_candidates(cfg, payload())
        assert [s['id'] for s in selected] == ['a', 'c', 'b']

    def test_max_attempts_truncates(self):
        cfg = config()
        cfg['policy']['maxAttempts'] = 2
        selected, skipped = select_candidates(cfg, payload())
        assert [s['id'] for s in selected] == ['a', 'b']
        # skipped does not record truncation — only incompatibility
        assert skipped == []

    def test_max_attempts_one_leader_only(self):
        cfg = config()
        cfg['policy']['maxAttempts'] = 1
        selected, _ = select_candidates(cfg, payload())
        assert [s['id'] for s in selected] == ['a']

    def test_selection_is_deepcopy(self):
        cfg = config()
        selected, _ = select_candidates(cfg, payload())
        selected[0]['enabled'] = False
        assert cfg['providers'][0]['enabled'] is True

    def test_empty_backups(self):
        cfg = config()
        cfg['roles']['backups'] = []
        selected, skipped = select_candidates(cfg, payload())
        assert [s['id'] for s in selected] == ['a']
        assert skipped == []
