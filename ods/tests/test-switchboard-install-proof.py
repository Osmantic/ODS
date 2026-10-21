"""Exercise host route scheduling without starting a real host service."""
import ast
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock


class InstallProof(unittest.TestCase):
    def test_idle_schedules_proof_but_active_lifecycle_cancels_it(self):
        tree = ast.parse((Path(__file__).parents[1] / 'bin/ods-host-agent.py').read_text(encoding='utf-8'))
        names = {'_model_status_allows_route_proof', '_verify_switchboard_route_for_status'}
        module = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[])
        schedule, cancel = Mock(), Mock()
        ns = dict(_switchboard_state=object(), _bootstrap_status_allows_route_proof=lambda: False,
                  _model_lifecycle_status=lambda: {}, _switchboard_state_path=lambda: Path('state'),
                  _switchboard_state_needs_current_env_verification=lambda path: True,
                  _schedule_initial_switchboard_verification=schedule, _switchboard_initial_verify_cancel=cancel)
        exec(compile(module, '<host proof functions>', 'exec'), ns)
        verify = ns['_verify_switchboard_route_for_status']
        verify({'status': 'idle'}, 'install')
        schedule.assert_called_once_with('install')
        schedule.reset_mock()
        ns['_model_lifecycle_status'] = lambda: {
            'lifecycleActive': True, 'activeOperation': 'model_activation'}
        verify({'status': 'idle'}, 'install')
        schedule.assert_not_called()
        cancel.set.assert_called_once()
        # A completed result remains observable but does not own the lifecycle.
        ns['_model_lifecycle_status'] = lambda: {
            'activationResult': {'outcome': 'rolled_back', 'failureCode': 'runtime_load_failed'}}
        verify({'status': 'idle'}, 'install')
        schedule.assert_called_once_with('install')
        cancel.set.assert_called_once()
        for state in ('failed', 'downloading', 'cancelled'):
            self.assertFalse(ns['_model_status_allows_route_proof']({'status': state}))

    def test_durable_model_hold_blocks_route_reproof_before_and_after_runtime_probe(self):
        tree = ast.parse((Path(__file__).parents[1] / 'bin/ods-host-agent.py').read_text(encoding='utf-8'))
        names = {'_prepare_initial_switchboard_verification', '_publish_verified_initial_switchboard_route'}
        module = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[])
        pending = {'value': True}
        state = Mock()
        state.migrate_env_identity.return_value = {'catalogId': 'model', 'contextLength': 65536}
        cancelled = Mock()
        ns = dict(
            _switchboard_state=state,
            _switchboard_initial_verify_cancel=cancelled,
            _model_lifecycle_state_lock=threading.Lock(),
            _model_lifecycle_operation=None,
            _pixel_model_transition_pending_for_route_proof=lambda: pending['value'],
            _switchboard_state_path=lambda: Path('state'),
            _switchboard_state_needs_current_env_verification=lambda path, env=None: True,
            _current_runtime_model_inputs=lambda env, identity: ('model.gguf', 'model'),
            _catalog_model_for_current_env=lambda env: ('model', {}),
            _initial_switchboard_backend=lambda env: ('lemonade', 'lemonade-default', 'model'),
            _wait_for_model_readiness=lambda *args, **kwargs: {'identity': 'model', 'contextLength': 65536},
            _initial_switchboard_route_env_matches=lambda env: True,
            _runtime_model_identity_matches=lambda *args, **kwargs: True,
            _model_agent_viable=lambda model, context: True,
            # Any-model WP4.1: only ODS_MODEL_PROFILES=enabled re-publishes measured
            # capabilities; observe keeps today's route record.
            _model_profiles_mode=lambda env: 'observe',
            _SWITCHBOARD_ROUTE_ENV_KEYS=(),
            load_env=lambda path: {},
            INSTALL_DIR=Path('/tmp'),
            logger=Mock(),
        )
        exec(compile(module, '<host proof functions>', 'exec'), ns)
        self.assertFalse(ns['_prepare_initial_switchboard_verification']())
        cancelled.set.assert_called_once()

        pending['value'] = False
        self.assertTrue(ns['_prepare_initial_switchboard_verification']())
        # Simulate a transaction acquiring its durable hold while the runtime
        # proof is in flight; no state write may invalidate its before-hash.
        def proof(*args, **kwargs):
            pending['value'] = True
            return {'identity': 'model', 'contextLength': 65536}

        ns['_wait_for_model_readiness'] = proof
        self.assertFalse(ns['_publish_verified_initial_switchboard_route'](reason='startup'))
        state.record_verified_route.assert_not_called()


if __name__ == '__main__':
    unittest.main()
