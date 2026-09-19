"""Complementary runtime_session edge coverage.

Extends tests/pixel_inference/test_provider_session.py with the paths it
does not exercise: handoff selection-scope validation, scope propagation
into the lease, activate() agent-scope mismatch, the reasoning env flag,
and serve() start-failure / status transitions.
"""
import json
import os
from pathlib import Path

import pytest

from test_provider_runtime import configuration
from pixel_provider.config import public_config
from pixel_provider.runtime_session import ProviderSession
from pixel_provider.store import StoreError
from pixel_provider.vault import CredentialStore
from pixel_provider.client import _json, _write_private, read_private

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX adapter')


@pytest.fixture
def saved(tmp_path):
    root = tmp_path / 'providers'
    root.mkdir(mode=0o700)
    config = configuration()
    config['revision'] = 0
    result = CredentialStore(root).save_public(
        {'document': public_config(config), 'expectedRevision': 0,
         'credentialChanges': {}})
    return root, result


class TestHandoffSelectionScope:
    def _with_handoff(self, saved):
        root, config = saved
        config['roles']['handoff'] = 'backup'
        CredentialStore(root).save_public(
            {'document': config, 'expectedRevision': 1,
             'credentialChanges': {}})
        return root

    def test_scope_without_provider_rejected(self, saved):
        root = self._with_handoff(saved)
        with pytest.raises(StoreError, match='invalid-handoff-selection-scope'):
            ProviderSession(root, expected_revision=2, confirmed=True,
                            handoff_selection_scope='task')

    @pytest.mark.parametrize('scope', ['task', 'conversation', 'default'])
    def test_valid_scopes_propagate_to_lease(self, saved, scope):
        root = self._with_handoff(saved)
        session = ProviderSession(root, expected_revision=2, confirmed=True,
                                  handoff_provider_id='backup',
                                  handoff_selection_scope=scope)
        assert session.handoff['selectionScope'] == scope
        with session.serve() as lease:
            assert lease['handoff']['selectionScope'] == scope

    def test_invalid_scope_rejected(self, saved):
        root = self._with_handoff(saved)
        with pytest.raises(StoreError, match='invalid-handoff-selection-scope'):
            ProviderSession(root, expected_revision=2, confirmed=True,
                            handoff_provider_id='backup',
                            handoff_selection_scope='forever')

    def test_no_scope_omits_key(self, saved):
        root = self._with_handoff(saved)
        session = ProviderSession(root, expected_revision=2, confirmed=True,
                                  handoff_provider_id='backup')
        assert 'selectionScope' not in session.handoff


class TestHandoffGuardrails:
    def test_handoff_to_same_as_leader_rejected(self, saved):
        root, config = saved
        config['roles']['handoff'] = 'primary'
        CredentialStore(root).save_public(
            {'document': config, 'expectedRevision': 1,
             'credentialChanges': {}})
        with pytest.raises(StoreError, match='handoff-recipient-incompatible'):
            ProviderSession(root, expected_revision=2, confirmed=True,
                            handoff_provider_id='primary')

    def test_handoff_reasoning_downgrade_rejected(self, saved):
        root, config = saved
        config['roles']['handoff'] = 'backup'
        config['providers'][0]['reasoning'] = True
        CredentialStore(root).save_public(
            {'document': config, 'expectedRevision': 1,
             'credentialChanges': {}})
        with pytest.raises(StoreError, match='handoff-recipient-incompatible'):
            ProviderSession(root, expected_revision=2, confirmed=True,
                            handoff_provider_id='backup')

class TestInitGuards:
    def test_expected_revision_must_be_int(self, saved):
        root, _ = saved
        with pytest.raises(StoreError, match='provider-confirmation-required'):
            ProviderSession(root, expected_revision='1', confirmed=True)
        with pytest.raises(StoreError, match='provider-confirmation-required'):
            ProviderSession(root, expected_revision=True, confirmed=True)

    def test_routing_disabled(self, saved):
        root, config = saved
        config['enabled'] = False
        CredentialStore(root).save_public(
            {'document': config, 'expectedRevision': 1,
             'credentialChanges': {}})
        with pytest.raises(StoreError, match='provider-routing-disabled'):
            ProviderSession(root, expected_revision=2, confirmed=True)

    def test_route_cycle_in_selected(self, saved):
        root, config = saved
        config['providers'][0]['model'] = 'ods/pixel'
        CredentialStore(root).save_public(
            {'document': config, 'expectedRevision': 1,
             'credentialChanges': {}})
        with pytest.raises(StoreError, match='provider-route-cycle'):
            ProviderSession(root, expected_revision=2, confirmed=True)

    def test_initial_status(self, saved):
        root, _ = saved
        session = ProviderSession(root, expected_revision=1, confirmed=True)
        assert session.status == 'not-started'
        assert session.events == []


class TestActivateScope:
    def _openclaw(self, tmp_path, agents):
        config = {'models': {'providers': {}},
                  'agents': {'defaults': {}, 'list': agents}}
        source = tmp_path / 'openclaw.json'
        _write_private(source, _json(config))
        return source

    def test_wrong_agent_id_rejected(self, saved, tmp_path):
        root, _ = saved
        session = ProviderSession(root, expected_revision=1, confirmed=True)
        source = self._openclaw(tmp_path, [{'id': 'other'}])
        run = tmp_path / 'run'
        run.mkdir(mode=0o700)
        with pytest.raises(StoreError, match='provider-client-scope-mismatch'):
            with session.activate(run, {'OPENCLAW_CONFIG_PATH': str(source)},
                                  'fixture'):
                pass

    def test_multiple_agents_rejected(self, saved, tmp_path):
        root, _ = saved
        session = ProviderSession(root, expected_revision=1, confirmed=True)
        source = self._openclaw(tmp_path, [{'id': 'fixture'},
                                         {'id': 'extra'}])
        run = tmp_path / 'run'
        run.mkdir(mode=0o700)
        with pytest.raises(StoreError, match='provider-client-scope-mismatch'):
            with session.activate(run, {'OPENCLAW_CONFIG_PATH': str(source)},
                                  'fixture'):
                pass

    def test_reasoning_env_flag(self, saved, tmp_path):
        root, config = saved
        config['providers'][0]['reasoning'] = True
        CredentialStore(root).save_public(
            {'document': config, 'expectedRevision': 1,
             'credentialChanges': {}})
        session = ProviderSession(root, expected_revision=2, confirmed=True)
        source = self._openclaw(tmp_path, [{'id': 'fixture'}])
        run = tmp_path / 'run'
        run.mkdir(mode=0o700)
        with session.activate(run, {'OPENCLAW_CONFIG_PATH': str(source)},
                              'fixture') as env:
            assert env['PIXEL_MODEL_REASONING'] == 'true'

    def test_reasoning_off_flag(self, saved, tmp_path):
        root, _ = saved
        session = ProviderSession(root, expected_revision=1, confirmed=True)
        source = self._openclaw(tmp_path, [{'id': 'fixture'}])
        run = tmp_path / 'run'
        run.mkdir(mode=0o700)
        with session.activate(run, {'OPENCLAW_CONFIG_PATH': str(source)},
                              'fixture') as env:
            assert env['PIXEL_MODEL_REASONING'] == 'false'

    def test_vision_model_inputs(self, saved, tmp_path):
        root, config = saved
        config['providers'][0]['supportsVision'] = True
        CredentialStore(root).save_public(
            {'document': config, 'expectedRevision': 1,
             'credentialChanges': {}})
        session = ProviderSession(root, expected_revision=2, confirmed=True)
        source = self._openclaw(tmp_path, [{'id': 'fixture'}])
        run = tmp_path / 'run'
        run.mkdir(mode=0o700)
        with session.activate(run, {'OPENCLAW_CONFIG_PATH': str(source)},
                              'fixture') as env:
            effective = json.loads(read_private(Path(
                env['OPENCLAW_CONFIG_PATH'])))
            provider = next(iter(effective['models']['providers'].values()))
            assert provider['models'][0]['input'] == ['text', 'image']

    def test_events_file_written_on_activate_exit(self, saved, tmp_path):
        root, _ = saved
        session = ProviderSession(root, expected_revision=1, confirmed=True)
        source = self._openclaw(tmp_path, [{'id': 'fixture'}])
        run = tmp_path / 'run'
        run.mkdir(mode=0o700)
        with session.activate(run, {'OPENCLAW_CONFIG_PATH': str(source)},
                              'fixture'):
            pass
        events = json.loads(read_private(run / 'provider-events.json'))
        assert events['revision'] == 1
        assert events['runtimeStatus'] == 'stopped'
        assert events['events'] == []


class TestServe:
    def test_lease_shape(self, saved):
        root, _ = saved
        session = ProviderSession(root, expected_revision=1, confirmed=True)
        with session.serve() as lease:
            assert lease['baseUrl'].startswith('http://127.0.0.1:')
            assert lease['token'].startswith('ods_route_')
            assert len(lease['token']) == len('ods_route_') + 64
            assert lease['contextTokens'] == 32768
            assert lease['maxOutputTokens'] == 4096
            assert lease['reasoning'] is False
            assert lease['supportsVision'] is False
            assert 'handoff' not in lease
            assert session.status == 'active-for-turn'
        assert session.status == 'stopped'

    def test_serve_status_stopped_after_exit(self, saved):
        root, _ = saved
        session = ProviderSession(root, expected_revision=1, confirmed=True)
        with session.serve():
            pass
        assert session.status == 'stopped'
