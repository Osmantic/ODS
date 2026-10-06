import copy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

SERVICE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ods_laya_service', SERVICE / 'server.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


def result(model):
    return {'routing': {'model': model},
            'usage': {'truncated': False, 'state_tokens_dropped': 0, 'truncated_questions': []},
            'answers': {
                'department': {'type': 'choice', 'choice': 'billing',
                               'probabilities': {'billing': 0.8, 'technical': 0.1, 'other': 0.1}},
                'urgency': {'type': 'score', 'score': 1.0,
                            'probabilities': {'0': 0.2, '1': 0.6, '2': 0.2},
                            'legend': {'0': 'low', '1': 'medium', '2': 'high'}},
                'refund': {'type': 'noul', 'noul': 0.8}}}


class Router:
    loaded = []

    def __init__(self):
        self.calls = []

    def predict(self, text, questions, **controls):
        self.calls.append((text, questions, controls))
        return result(controls['model'])


class ServiceTest(unittest.TestCase):
    def test_all_checkpoint_startups_precede_readiness_and_auth_is_required(self):
        router = Router()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, clear=False):
            key = Path(directory) / 'key'
            key.write_text('a' * 64, encoding='ascii')
            app = service.create_portal_app(router=router, key_file=key)
            self.assertEqual([call[2]['model'] for call in router.calls],
                             ['english', 'multilingual', 'typed-decisions'])
            with TestClient(app) as client:
                ready = client.get('/ready')
                self.assertEqual(ready.status_code, 200)
                self.assertEqual(ready.json(), {'status': 'ok', 'startupInferenceVerified': True})
                for headers in [{}, {'Authorization': 'Bearer wrong'}]:
                    reply = client.post('/v1/systemone/batch', json={}, headers=headers)
                    self.assertEqual(reply.status_code, 401)
                self.assertEqual(len(router.calls), 3)
                self.assertNotIn('a' * 64, ready.text)

    def test_failed_prediction_never_creates_a_ready_app(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, clear=False):
            key = Path(directory) / 'key'
            key.write_text('b' * 64, encoding='ascii')
            router = Router()
            with patch.object(router, 'predict', side_effect=RuntimeError('inference unavailable')):
                with self.assertRaisesRegex(RuntimeError, 'inference unavailable'):
                    service.create_portal_app(router=router, key_file=key)

    def test_invalid_key_is_not_echoed_and_no_inference_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'key'
            key.write_text('private invalid value', encoding='ascii')
            router = Router()
            with self.assertRaisesRegex(ValueError, '^Invalid managed Laya API key$'):
                service.create_portal_app(router=router, key_file=key)
            self.assertEqual(router.calls, [])

    def test_missing_context_and_malformed_decisions_fail_startup(self):
        baseline = result('english')
        broken = []
        for field, value in [('truncated', True), ('state_tokens_dropped', 1), ('truncated_questions', ['refund'])]:
            item = copy.deepcopy(baseline)
            item['usage'][field] = value
            broken.append(item)
        item = copy.deepcopy(baseline)
        del item['answers']['refund']
        broken.append(item)
        for value in [float('nan'), float('inf'), -0.1, 1.1, True]:
            item = copy.deepcopy(baseline)
            item['answers']['refund']['noul'] = value
            broken.append(item)
        item = copy.deepcopy(baseline)
        item['answers']['department']['probabilities']['billing'] = 0.1
        broken.append(item)
        for item in broken:
            with self.subTest(result=item), self.assertRaises(ValueError):
                service.validate_startup_result(item, 'english')
        service.validate_startup_result(baseline, 'english')


if __name__ == '__main__':
    unittest.main()
