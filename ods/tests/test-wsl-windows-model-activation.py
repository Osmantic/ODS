"""Exercise the real host-agent helpers, with a fake inference transport."""
import importlib.util
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
spec = importlib.util.spec_from_file_location('wsl_activation_agent', ROOT / 'bin/ods-host-agent.py')
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(AMD_INFERENCE_RUNTIME_MODE='wsl-windows-lemonade',
                        AMD_INFERENCE_RUNTIME='lemonade', AMD_INFERENCE_LOCATION='host',
                        AMD_INFERENCE_MANAGED='true', GPU_BACKEND='amd',
                        GGUF_FILE='test.gguf', CTX_SIZE='8192', AMD_INFERENCE_PORT='18080')
        self.wsl = patch.object(agent, '_is_wsl_linux', return_value=True)
        self.wsl.start()
        self.addCleanup(self.wsl.stop)

    def test_existing_platforms_are_not_redirected(self):
        for override in [dict(GPU_BACKEND='nvidia'), dict(AMD_INFERENCE_RUNTIME_MODE=''),
                         dict(LEMONADE_EXTERNAL='true'), dict(EXTERNAL_LLM_URL='http://example.test'),
                         dict(AMD_INFERENCE_MANAGED='false')]:
            self.assertFalse(agent._is_wsl_windows_lemonade({**self.env, **override}))
        for override in [dict(AMD_INFERENCE_RUNTIME='llama-server'), dict(AMD_INFERENCE_LOCATION='container')]:
            self.assertFalse(agent._is_wsl_windows_lemonade({**self.env, **override}))
        with patch.object(agent, '_is_wsl_linux', return_value=False):
            self.assertFalse(agent._is_wsl_windows_lemonade(self.env))

    def test_load_uses_live_catalog_and_explicit_context(self):
        catalog = {'data': [{'id': 'extra.test.gguf', 'checkpoint': 'D:\\models\\test.gguf'}]}
        with patch.object(agent, '_wsl_windows_lemonade_request', side_effect=[catalog, {'status': 'success'}]) as call:
            agent._stage_wsl_windows_lemonade(self.env)
        self.assertEqual(call.call_args.kwargs['body'], {'model_name': 'extra.test.gguf', 'ctx_size': 8192, 'llamacpp_backend': 'vulkan'})

    def test_missing_catalog_model_cannot_be_guessed(self):
        with patch.object(agent, '_wsl_windows_lemonade_request', return_value={'data': []}) as call:
            with self.assertRaises(RuntimeError):
                agent._stage_wsl_windows_lemonade(self.env)
        self.assertEqual(call.call_count, 1)

    def test_readiness_requires_matching_context_and_completion(self):
        health = {'status': 'ok', 'model_loaded': 'extra.test.gguf', 'all_models_loaded': [{
            'model_name': 'extra.test.gguf', 'checkpoint': 'D:\\models\\test.gguf',
            'recipe_options': {'ctx_size': 8192, 'llamacpp_backend': 'vulkan'}, 'device': 'gpu'}]}
        completion = {'model': 'test.gguf', 'choices': [{'message': {'content': 'Ready.'}}]}
        with patch.object(agent, '_wsl_windows_lemonade_request', side_effect=[health, completion]):
            proof = agent._wsl_windows_lemonade_readiness(self.env, 'test.gguf', 'extra.test.gguf', exact_context=True)
        self.assertEqual(proof['contextLength'], 8192)
        for context in [4096, 16384]:
            health['all_models_loaded'][0]['recipe_options']['ctx_size'] = context
            with patch.object(agent, '_wsl_windows_lemonade_request', return_value=health) as call:
                self.assertEqual(agent._wsl_windows_lemonade_readiness(self.env, 'test.gguf', 'extra.test.gguf', exact_context=True), {})
            self.assertEqual(call.call_count, 1)

    def test_cpu_fallback_is_not_gpu_success(self):
        for device, backend in [('cpu', 'vulkan'), ('gpu', 'cpu')]:
            health = {'status': 'ok', 'model_loaded': 'extra.test.gguf', 'all_models_loaded': [{
                'model_name': 'extra.test.gguf', 'checkpoint': 'D:\\models\\test.gguf',
                'recipe_options': {'ctx_size': 8192, 'llamacpp_backend': backend}, 'device': device}]}
            with patch.object(agent, '_wsl_windows_lemonade_request', return_value=health) as call:
                self.assertEqual(agent._wsl_windows_lemonade_readiness(self.env, 'test.gguf', 'extra.test.gguf', exact_context=True), {})
            self.assertEqual(call.call_count, 1)

    def test_cancelled_verification_does_not_call_runtime(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(agent, '_wsl_windows_lemonade_request') as call:
            proof = agent._wait_for_model_readiness(self.env, model_id='test', gguf_file='test.gguf',
                       llm_model_name='test', initial_delay=0, attempts=1, return_proof=True, cancel_event=cancel)
        self.assertEqual(proof, {})
        call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
