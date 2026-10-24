#!/usr/bin/env python3
"""Exercise installed inference diagnostics against real private HTTP servers."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]


class Endpoint:
    def __init__(self):
        self.requests = []
        self.model = 'fixture/"loaded-model'
        self.token = None
        self.models = None
        self.models_status = 200
        self.reply = None
        self.status = 200
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.respond(None)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = 'invalid JSON'
                self.respond(body)

            def respond(self, body):
                endpoint.requests.append((self.path, self.headers.get('Authorization'), body))
                status, reply = 200, {'status': 'ok'}
                if self.path.startswith('/v1/models'):
                    status = endpoint.models_status
                    reply = endpoint.models if endpoint.models is not None else {'data': [{'id': endpoint.model}]}
                elif self.path == '/v1/chat/completions':
                    if not isinstance(body, dict) or body.get('model') != endpoint.model:
                        status, reply = 400, {'error': 'wrong model or malformed JSON'}
                    elif endpoint.reply is not None:
                        status, reply = endpoint.status, endpoint.reply
                    elif body.get('tools'):
                        expected = body['tools'][0]['function']
                        if expected['name'] != 'get_weather' or 'city' not in expected['parameters']['required']:
                            status, reply = 400, {'error': 'wrong tool schema'}
                        else:
                            reply = {'choices': [{'message': {'content': None, 'tool_calls': [
                                {'id': 'call_fixture', 'type': 'function', 'function': {
                                    'name': 'get_weather', 'arguments': json.dumps({'city': 'Tokyo'})}}
                            ]}, 'finish_reason': 'tool_calls'}]}
                    else:
                        reply = {'model': endpoint.model, 'choices': [{'message': {'content': '4'}}]}
                elif self.path == '/v1/audio/voices':
                    reply = {'voices': [{'voice_id': 'af_bella'}]}
                elif self.path == '/v1/audio/speech':
                    reply = 'RIFF' + 'a' * 2048
                elif self.path == '/embed':
                    reply = '[[0.1,0.2]]'
                elif self.path == '/v1/audio/transcriptions':
                    reply = {'text': 'hello world'}
                if self.path.startswith('/v1/models') or self.path == '/v1/chat/completions':
                    want = f'Bearer {endpoint.token}' if endpoint.token else None
                    if self.headers.get('Authorization') != want:
                        status, reply = 401, {'error': 'wrong authentication'}
                raw = (reply if isinstance(reply, str) else json.dumps(reply, indent=2)).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class InferenceDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ods-inference-contract-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'extensions/services').mkdir(parents=True)
        shutil.copytree(ROOT / 'lib', self.root / 'lib')
        for name in ('ods-test.sh', 'ods-test-functional.sh'):
            text = (ROOT / 'scripts' / name).read_text()
            # The unrelated audio probes retain their production behavior,
            # with private artifact names so this test cannot touch /tmp peers.
            text = text.replace('/tmp/test_tts_output.wav', str(self.root / 'tts.wav'))
            text = text.replace('/tmp/test_audio.wav', str(self.root / 'audio.wav'))
            (self.root / 'scripts' / name).write_text(text)
        self.primary = Endpoint()
        self.unrelated = Endpoint()
        self.addCleanup(self.primary.close)
        self.addCleanup(self.unrelated.close)
        self.key = secrets.token_urlsafe(24)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith((
            'LLM_', 'LITELLM_', 'ODS_', 'OLLAMA_', 'EXTERNAL_LLM_', 'NATIVE_LLM_'))}
        self.env.update(HOME=str(self.root), INSTALL_DIR=str(self.root), ODS_HOME=str(self.root),
                        ODS_DIR=str(self.root), ENV_FILE=str(self.root / '.env'))
        (self.root / 'bin').mkdir()
        curl = shutil.which('curl')
        self.assertIsNotNone(curl)
        (self.root / 'bin/curl').write_text(
            '#!/bin/bash\nprintf "%s\\0" "$@" >> "$CURL_ARG_LOG"\nexec ' + curl + ' "$@"\n')
        (self.root / 'bin/curl').chmod(0o755)
        self.env['PATH'] = str(self.root / 'bin') + os.pathsep + self.env['PATH']
        self.env['CURL_ARG_LOG'] = str(self.root / 'curl-args')

    def run_probe(self, consumer='llm', gateway=False, extra=None, config_extra=None, quick=False):
        config = {'ODS_MODEL_SWITCHBOARD': 'enabled' if gateway else 'disabled',
                  'OLLAMA_PORT': str(self.unrelated.port if gateway else self.primary.port),
                  'LITELLM_PORT': str(self.primary.port), 'LITELLM_KEY': self.key,
                  'WHISPER_PORT': str(self.primary.port), 'TTS_PORT': str(self.primary.port)}
        config.update(config_extra or {})
        if gateway:
            self.primary.model = 'ods/current' if config['ODS_MODEL_SWITCHBOARD'] == 'enabled' else 'default'
            self.primary.token = self.key
        (self.root / '.env').write_text(''.join(f'{key}={value}\n' for key, value in config.items()))
        args = ['bash', str(self.root / 'scripts/ods-test.sh'), '--json', '--service', consumer]
        if quick:
            args.append('--quick')
        if consumer == 'functional':
            args = ['bash', str(self.root / 'scripts/ods-test-functional.sh')]
        speech = {key: f'http://127.0.0.1:{self.primary.port}'
                  for key in ('WHISPER_URL', 'TTS_URL', 'EMBEDDING_URL')}
        result = subprocess.run(args, env={**self.env, **speech, **(extra or {})}, text=True,
                                capture_output=True, timeout=20)
        self.assertNotIn(self.key, result.stdout + result.stderr)
        log = self.root / 'curl-args'
        if log.exists():
            self.assertNotIn(self.key.encode(), log.read_bytes(), 'gateway key exposed in curl argv')
        return result

    def assert_llm_result(self, result, consumer, success):
        if consumer == 'functional':
            self.assertIn('LLM generates' if success else 'LLM ', result.stdout)
            self.assertEqual(result.returncode, 0 if success else 1, result.stdout + result.stderr)
            if success:
                self.assertIn('LLM generates correct answer', result.stdout)
            else:
                self.assertNotIn('LLM generates', result.stdout)
        else:
            rows = {row['name']: row for row in json.loads(result.stdout)['results']}
            self.assertEqual(rows.get('LLM Inference', rows.get('LLM Health'))['status'],
                             'pass' if success else 'fail', result.stdout)
            self.assertEqual(result.returncode, 0 if success else 1, result.stderr)

    def test_direct_model_json_and_all_selected_gateway_consumers(self):
        self.assert_llm_result(self.run_probe(), 'llm', True)
        for consumer in ('llm', 'tool-calling', 'voice-roundtrip', 'functional'):
            with self.subTest(consumer=consumer):
                self.primary.requests.clear()
                result = self.run_probe(consumer, gateway=True)
                if consumer in ('llm', 'functional'):
                    self.assert_llm_result(result, consumer, True)
                else:
                    rows = json.loads(result.stdout)['results']
                    self.assertEqual(rows[-1]['status'], 'pass', result.stdout)
                chats = [body for path, _auth, body in self.primary.requests if path == '/v1/chat/completions']
                self.assertTrue(chats)
                self.assertTrue(all(body['model'] == 'ods/current' for body in chats))
                self.assertEqual(self.unrelated.requests, [], 'selected gateway fell back to unrelated local runtime')

    def test_http_errors_and_non_answers_cannot_pass(self):
        for status, reply in ((503, {'choices': [{'message': {'content': 'error answer'}}]}),
                              (200, {'error': {'content': 'not an answer'}}),
                              (200, {'choices': [{'message': {'content': None}}]}),
                              (200, {'choices': [{'message': {'content': '   '}}]}),
                              (200, 'not JSON')):
            self.primary.status, self.primary.reply = status, reply
            for consumer in ('llm', 'functional', 'voice-roundtrip'):
                with self.subTest(status=status, reply=reply, consumer=consumer):
                    result = self.run_probe(consumer)
                    if consumer == 'voice-roundtrip':
                        self.assertEqual(result.returncode, 1, result.stdout)
                        self.assertEqual(json.loads(result.stdout)['results'][-1]['status'], 'fail')
                    else:
                        self.assert_llm_result(result, consumer, False)

    def test_discovery_failure_never_sends_a_guessed_model(self):
        for status, models in ((200, {'data': []}), (200, {'error': {'id': 'not a model'}}),
                               (200, 'not JSON'), (503, {'data': [{'id': 'not usable'}]})):
            for consumer in ('llm', 'tool-calling', 'voice-roundtrip', 'functional'):
                with self.subTest(status=status, models=models, consumer=consumer):
                    self.primary.requests.clear()
                    self.primary.models_status, self.primary.models = status, models
                    result = self.run_probe(consumer)
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertFalse(any(path == '/v1/chat/completions' for path, *_ in self.primary.requests))

    def test_gateway_errors_and_missing_key_never_probe_local_fallback(self):
        self.primary.reply = {'choices': [{'message': {'content': 'error body'}}]}
        self.primary.status = 503
        self.assert_llm_result(self.run_probe(gateway=True), 'llm', False)
        self.assertEqual(self.unrelated.requests, [])
        self.primary.requests.clear()
        result = self.run_probe(gateway=True, config_extra={'LITELLM_KEY': ''})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.primary.requests, [])
        self.assertEqual(self.unrelated.requests, [])

    def test_cloud_and_host_native_routes_use_authenticated_gateway_default(self):
        for route in ({'ODS_MODE': 'cloud'}, {'NATIVE_LLM_BASE_URL': 'http://127.0.0.1:1'}):
            with self.subTest(route=route):
                self.assert_llm_result(self.run_probe(gateway=True, config_extra={
                    'ODS_MODEL_SWITCHBOARD': 'disabled', **route}), 'llm', True)
                self.assertEqual(self.unrelated.requests, [])

    def test_quick_mode_probes_selected_models_without_generating(self):
        result = self.run_probe(gateway=True, quick=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)['results'][-1]['status'], 'skip')
        self.assertFalse(any(path == '/v1/chat/completions' for path, *_ in self.primary.requests))

    def test_tool_key_alone_is_not_a_function_invocation(self):
        for call in ([], [{'type': 'function', 'function': {'name': 'wrong', 'arguments': '{}'}}],
                     [{'type': 'function', 'function': {'name': 'get_weather', 'arguments': 'not JSON'}}],
                     [{'type': 'function', 'function': {'name': 'get_weather', 'arguments': '{\"city\":\"Paris\"}'}}]):
            self.primary.reply = {'choices': [{'message': {'tool_calls': call}}]}
            result = self.run_probe('tool-calling')
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertEqual(json.loads(result.stdout)['results'][-1]['status'], 'fail')

        self.primary.status = 503
        self.primary.reply = {'choices': [{'message': {'tool_calls': [
            {'type': 'function', 'function': {'name': 'get_weather', 'arguments': '{"city":"Tokyo"}'}}
        ]}}]}
        result = self.run_probe('tool-calling')
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_explicit_url_and_host_port_keep_direct_override(self):
        # Caller override is deliberate; never send a gateway key to that URL.
        for override in ({'LLM_URL': f'http://127.0.0.1:{self.unrelated.port}'},
                         {'LLM_HOST': '127.0.0.1', 'LLM_PORT': str(self.unrelated.port)}):
            with self.subTest(override=override):
                self.primary.requests.clear()
                self.assert_llm_result(self.run_probe(gateway=True, extra=override), 'llm', True)
                self.assertEqual(self.primary.requests, [])
                self.assertTrue(self.unrelated.requests)


if __name__ == '__main__':
    unittest.main(verbosity=2)
