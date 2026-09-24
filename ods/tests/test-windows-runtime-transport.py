import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('transport', Path(__file__).resolve().parents[1] / 'bin/model_switchboard/windows_transport.py')
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


class WindowsRuntimeTransportTests(unittest.TestCase):
    def test_credentials_use_stdin_and_destination_is_fixed(self):
        response = subprocess.CompletedProcess([], 0, json.dumps({'status': 200, 'body': '{"status":"ok"}'}))
        with patch.object(transport.subprocess, 'run', return_value=response) as run:
            self.assertEqual(transport.request_json('GET', '/api/v1/health', port=18080, api_key='test-secret'), {'status': 'ok'})
        args, kwargs = run.call_args
        self.assertNotIn('test-secret', ' '.join(args[0]))
        self.assertEqual(json.loads(kwargs['input'])['key'], 'test-secret')
        self.assertEqual(args[0][:5], ['docker', 'exec', '-i', 'ods-dashboard-api', 'python3'])
        self.assertNotIn('shell', kwargs)

    def test_uncertain_load_is_not_replayed(self):
        with patch.object(transport.subprocess, 'run', side_effect=subprocess.TimeoutExpired('docker', 10)) as run:
            with self.assertRaises(transport.WindowsRuntimeTransportError):
                transport.request_json('POST', '/api/v1/load', port=18080, body={'model_name': 'test'})
        self.assertEqual(run.call_count, 1)

    def test_unrelated_operations_never_start_a_process(self):
        with patch.object(transport.subprocess, 'run') as run:
            for method, path in [('POST', '/internal/set'), ('GET', '//example.com'), ('GET', '/api/v1/health?redirect=1')]:
                with self.assertRaises(ValueError):
                    transport.request_json(method, path, port=18080)
            for port in [0, 65536, True, '18080']:
                with self.assertRaises(ValueError):
                    transport.request_json('GET', '/api/v1/health', port=port)
        run.assert_not_called()

    def test_auth_header_injection_is_rejected(self):
        with patch.object(transport.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                transport.request_json('GET', '/api/v1/health', port=18080, api_key='key\r\nOther: value')
        run.assert_not_called()

    def test_http_failure_and_invalid_json_never_become_success(self):
        for response in [{'status': 503, 'body': '{"status":"ok"}'}, {'status': 200, 'body': '[]'}, {'status': 200, 'body': 'not-json'}]:
            with patch.object(transport.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(response))):
                with self.assertRaises(transport.WindowsRuntimeTransportError):
                    transport.request_json('GET', '/api/v1/health', port=18080)

    def test_child_errors_do_not_disclose_secrets(self):
        with patch.object(transport.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'test-secret')):
            with self.assertRaises(transport.WindowsRuntimeTransportError) as error:
                transport.request_json('GET', '/api/v1/health', port=18080, api_key='test-secret')
        self.assertNotIn('test-secret', str(error.exception))


if __name__ == '__main__':
    unittest.main()
