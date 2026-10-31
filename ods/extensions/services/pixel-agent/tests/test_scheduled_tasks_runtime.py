"""Real native cron fires after a gateway restart with no browser involved.

The loopback provider returns a controlled answer, not model inference. Nothing
uses the installed owner's config, models, credentials, plugins or services.
"""
from datetime import datetime, timedelta, timezone
import http.server
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / 'bin'), str(Path(__file__).resolve().parents[1] / 'host')]
from pixel_schedules import OPENCLAW_VERSION  # noqa: E402
from scheduled_tasks_owner import NativeTasks, gateway_call  # noqa: E402

PACKAGE = os.environ.get('OPENCLAW_PACKAGE_DIR')
ANSWER = 'Synthetic agenda: one meeting at 09:00. This is a controlled test result.'
MARKER = 'ods-native-scheduled-agenda-fixture'


@unittest.skipUnless(PACKAGE and sys.platform == 'linux', 'Set OPENCLAW_PACKAGE_DIR for the real Linux/WSL scheduler check')
class NativeSchedulerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.package = Path(PACKAGE)
        self.assertTrue(self.package.is_absolute())
        self.assertEqual(json.loads((self.package / 'package.json').read_bytes())['version'], OPENCLAW_VERSION)
        self.openclaw = str(self.package / 'openclaw.mjs')
        self.temporary = tempfile.TemporaryDirectory(prefix='ods-native-schedules-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.state = self.home / '.openclaw'
        self.state.mkdir(mode=0o700)
        self.env = {'PATH': os.environ['PATH'], 'HOME': str(self.home), 'NO_COLOR': '1',
                    'OPENCLAW_STATE_DIR': str(self.state), 'OPENCLAW_CONFIG_PATH': str(self.state / 'openclaw.json')}
        self.provider_requests = []
        requests = self.provider_requests

        class Provider(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_POST(handler):
                size = int(handler.headers['Content-Length'])
                if size > 1024 * 1024:
                    handler.send_error(413)
                    return
                requests.append(json.loads(handler.rfile.read(size)))
                payload = [{'id': 'synthetic-schedule', 'object': 'chat.completion.chunk',
                            'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ANSWER}, 'finish_reason': None}]},
                           {'id': 'synthetic-schedule', 'object': 'chat.completion.chunk',
                            'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}]
                raw = (''.join('data: ' + json.dumps(item) + '\n\n' for item in payload) + 'data: [DONE]\n\n').encode()
                handler.send_response(200)
                handler.send_header('Content-Type', 'text/event-stream')
                handler.send_header('Content-Length', str(len(raw)))
                handler.end_headers()
                handler.wfile.write(raw)

            def log_message(handler, *_args):
                pass

        self.provider = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        self.provider.daemon_threads = True
        thread = threading.Thread(target=self.provider.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.provider.server_close)
        self.addCleanup(self.provider.shutdown)
        self.token = secrets.token_hex(24)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            self.port = listener.getsockname()[1]
        config = {'gateway': {'mode': 'local', 'port': self.port, 'bind': 'loopback',
                             'auth': {'mode': 'token', 'token': self.token}},
                  'logging': {'file': str(self.home / 'gateway.log')},
                  'plugins': {'allow': [], 'entries': {}},
                  'models': {'providers': {'schedule-fixture': {
                      'api': 'openai-completions', 'apiKey': 'synthetic-no-auth',
                      'baseUrl': 'http://127.0.0.1:%d/v1' % self.provider.server_port,
                      'models': [{'id': 'synthetic-agenda', 'name': 'Synthetic fixture', 'contextWindow': 32768,
                                  'maxTokens': 1024, 'reasoning': False, 'input': ['text']}]}}},
                  'agents': {'defaults': {'heartbeat': {'every': '0m'}}, 'list': [{'id': 'pixel', 'default': True,
                      'workspace': str(self.home / 'workspace'), 'model': 'schedule-fixture/synthetic-agenda'}]}}
        path = self.state / 'openclaw.json'
        path.write_text(json.dumps(config))
        path.chmod(0o600)

    def start(self, label):
        output_path = self.home / (label + '.log')
        output = output_path.open('w')
        self.addCleanup(output.close)
        child = subprocess.Popen([self.openclaw, 'gateway', 'run', '--port', str(self.port)],
                                 cwd=self.home, env=self.env, stdout=output, stderr=subprocess.STDOUT)
        self.addCleanup(self.stop, child)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.assertIsNone(child.poll(), output_path.read_text())
            try:
                with socket.create_connection(('127.0.0.1', self.port), timeout=0.2):
                    return child
            except OSError:
                time.sleep(0.1)
        self.fail('Private gateway did not start within 30 seconds')

    @staticmethod
    def stop(child):
        if child.poll() is not None:
            return
        child.terminate()
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)

    def test_restart_background_result_pause_and_removal(self):
        child = self.start('before')
        with patch.dict(os.environ, self.env, clear=True):
            call = gateway_call(self.openclaw, self.port, self.token)
            tasks = NativeTasks(self.state / '.ods-scheduled-tasks', call)
            at = (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat(timespec='seconds')
            request = {'action': 'create', 'requestId': str(uuid.uuid4()), 'name': 'Synthetic agenda',
                       'task': MARKER + ': summarize the synthetic agenda.', 'schedule': {'kind': 'at', 'at': at}}
            created = tasks.operate(request)
            self.assertEqual(created['outcome'], 'applied', created)
            task = created['task']
            # The native database, not a mounted component or this adapter,
            # owns the schedule. Restart it before the run is due.
            self.stop(child)
            self.start('after')
            deadline = time.monotonic() + 75
            history = None
            while time.monotonic() < deadline:
                history = tasks.operate({'action': 'runs', 'id': task['id'], 'offset': 0})
                if history['items']:
                    break
                time.sleep(1)
            self.assertTrue(history['items'], (self.home / 'gateway.log').read_text()[-6000:])
            result = history['items'][0]
            self.assertEqual(result['status'], 'ok', result)
            self.assertIn(ANSWER, result['summary'])
            self.assertEqual(len(self.provider_requests), 1)
            self.assertIn(MARKER, json.dumps(self.provider_requests[0]))
            self.assertEqual(self.provider_requests[0]['model'], 'synthetic-agenda')
            current = tasks.operate({'action': 'list', 'offset': 0})['items'][0]
            removed = tasks.operate({'action': 'remove', 'id': task['id'], 'revision': current['revision']})
            self.assertTrue(removed['task']['removed'])
            self.assertEqual(tasks.operate({'action': 'list', 'offset': 0})['items'], [])
            self.assertEqual(tasks.operate({'action': 'archived', 'offset': 0})['items'][0]['id'], task['id'])
            retained = tasks.operate({'action': 'runs', 'id': task['id'], 'offset': 0})
            self.assertEqual(retained, history)
            future = tasks.operate(dict(request, requestId=str(uuid.uuid4()),
                schedule={'kind': 'at', 'at': '2030-01-01T09:00:00Z'}))['task']
            paused = tasks.operate({'action': 'set-enabled', 'id': future['id'], 'revision': future['revision'], 'enabled': False})['task']
            self.assertFalse(paused['enabled'])
            resumed = tasks.operate({'action': 'set-enabled', 'id': future['id'], 'revision': paused['revision'], 'enabled': True})['task']
            self.assertTrue(resumed['enabled'])
            tasks.operate({'action': 'remove', 'id': future['id'], 'revision': resumed['revision']})


if __name__ == '__main__':
    unittest.main()
