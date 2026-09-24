"""Exercise host route scheduling without starting a real host service."""
import ast
from pathlib import Path
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
        ns['_model_lifecycle_status'] = lambda: {'operation': 'model_activation'}
        verify({'status': 'idle'}, 'install')
        schedule.assert_not_called()
        cancel.set.assert_called_once()
        for state in ('failed', 'downloading', 'cancelled'):
            self.assertFalse(ns['_model_status_allows_route_proof']({'status': state}))


if __name__ == '__main__':
    unittest.main()
