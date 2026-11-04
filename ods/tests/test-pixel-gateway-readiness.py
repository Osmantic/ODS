#!/usr/bin/env python3
"""Run the production readiness loop against synthetic unit state and real HTTP."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / 'installers/lib/pixel-host-install.sh'


@contextmanager
def endpoint(body=b'{"ok":true,"status":"live"}', *, delay=0, drip=False):
    stopped = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests.append(self.path)
            try:
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if drip:
                    for byte in body:
                        if stopped.wait(.04):
                            break
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = HTTPServer(('127.0.0.1', 0), Handler, bind_and_activate=False)
    server.server_bind()  # Reserve the ephemeral port without listening yet.
    server.timeout = .05

    def serve():
        if not stopped.wait(delay):
            server.server_activate()
            while not stopped.is_set():
                server.handle_request()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield server.server_address[1], requests
    finally:
        stopped.set()
        worker.join(2)
        server.server_close()
        if worker.is_alive():
            raise AssertionError('HTTP readiness fixture did not stop')


class GatewayReadiness(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='ods-gateway-ready-')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.binary = self.root / 'bin'
        self.binary.mkdir()
        self.state = self.root / 'state.json'
        self.marker = self.root / 'restarted'
        self.verified = self.root / 'verified'
        script = f'''#!{sys.executable}
import json,os,pathlib,sys,time
state=json.loads(pathlib.Path(os.environ['ODS_GATEWAY_QA_STATE']).read_text())
args=sys.argv[1:]
if args==['show','openclaw-gateway.service','-p','MainPID','--value']:
    if state.get('restartRequired') and not pathlib.Path(os.environ['ODS_GATEWAY_QA_RESTART']).exists():
        print(111)
    elif state['pid']=='hung':
        time.sleep(10)
    else:
        print({{'absent':0,'unchanged':111,'new':222}}[state['pid']])
elif args==['is-active','--quiet','openclaw-gateway.service']:
    sys.exit(0 if state['active'] else 3)
else:
    raise SystemExit('unexpected fixture systemctl request')
'''
        self.executable('systemctl', script)
        if shutil.which('timeout') is None:
            # macOS test runners need only this command boundary fixture. The
            # production function is Linux-only and uses coreutils timeout.
            self.executable('timeout', f'''#!{sys.executable}
import subprocess,sys
assert sys.argv[1]=='--kill-after=1'
try:
    result=subprocess.run(sys.argv[3:],timeout=float(sys.argv[2]))
except subprocess.TimeoutExpired:
    raise SystemExit(124) from None
raise SystemExit(result.returncode)
''')

    def executable(self, name, text):
        path = self.binary / name
        path.write_text(text)
        path.chmod(0o755)

    def probe(self, port, *, pid='new', active=True, budget=.5, complete=False, verify_exit=0):
        self.state.write_text(json.dumps(dict(pid=pid, active=active, restartRequired=complete)))
        environment = {**os.environ, 'PATH': str(self.binary) + os.pathsep + os.environ['PATH'],
                       'ODS_GATEWAY_QA_STATE': str(self.state), 'ODS_GATEWAY_QA_RESTART': str(self.marker),
                       'ODS_GATEWAY_QA_VERIFIED': str(self.verified), 'ODS_GATEWAY_QA_VERIFY_EXIT': str(verify_exit),
                       'PIXEL_GATEWAY_PORT': '1'}  # Must use the installed-port adapter below.
        script = '''source "$1"
if [[ "$4" == complete ]]; then
    _ods_pixel_installed_gateway_port() { printf '%s\\n' "$QA_PORT"; }
    ods_sudo_available() { return 0; }
    ods_sudo() {
        [[ "$*" == 'systemctl restart openclaw-gateway.service' ]] || return 99
        : > "$ODS_GATEWAY_QA_RESTART"
    }
    _ods_pixel_verify_current_runtime() {
        : > "$ODS_GATEWAY_QA_VERIFIED"
        return "$ODS_GATEWAY_QA_VERIFY_EXIT"
    }
    QA_PORT=$2
    _ods_pixel_restart_gateway_and_verify fixture /fixture /fixture/pixel
else
    _ods_pixel_wait_restarted_gateway 111 "$2" "$3"
fi
'''
        started = time.monotonic()
        result = subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c', script, 'fixture',
                                 str(INSTALLER), str(port), str(budget), 'complete' if complete else 'wait'],
                                env=environment, text=True, capture_output=True, timeout=12)
        return result, time.monotonic() - started

    def test_delayed_listener_shares_one_process_and_http_budget(self):
        with endpoint(delay=.8) as (port, requests):
            result, elapsed = self.probe(port, budget=3.5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(elapsed, .7)
        self.assertLess(elapsed, 5)
        self.assertEqual(requests, ['/health'])

    def test_missing_or_unchanged_pid_never_reaches_http(self):
        for pid in ('absent', 'unchanged'):
            with self.subTest(pid=pid), endpoint() as (port, requests):
                result, elapsed = self.probe(port, pid=pid)
            self.assertNotEqual(result.returncode, 0)
            self.assertLess(elapsed, 3)
            self.assertIn('waiting for process', result.stderr)
            self.assertEqual(requests, [])

    def test_hung_systemctl_cannot_extend_deadline(self):
        with endpoint() as (port, requests):
            result, elapsed = self.probe(port, pid='hung')
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 3)
        self.assertEqual(requests, [])

    def test_inactive_unit_never_reaches_http(self):
        with endpoint() as (port, requests):
            result, elapsed = self.probe(port, active=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 3)
        self.assertEqual(requests, [])

    def test_invalid_health_responses_stay_fail_closed(self):
        for body in (b'not-json', b'{"ok":false,"status":"live"}', b'{"ok":1,"status":"live"}',
                     b'{"ok":true,"status":"starting"}'):
            with self.subTest(body=body), endpoint(body) as (port, requests):
                result, elapsed = self.probe(port)
            self.assertNotEqual(result.returncode, 0)
            self.assertLess(elapsed, 3)
            self.assertIn('waiting for endpoint', result.stderr)
            self.assertTrue(requests)

    def test_dripping_health_response_cannot_extend_deadline(self):
        with endpoint(drip=True) as (port, requests):
            result, elapsed = self.probe(port)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 3)
        self.assertTrue(requests)

    def test_full_verifier_failure_is_propagated(self):
        with endpoint() as (port, requests):
            result, _ = self.probe(port, complete=True, verify_exit=17)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertTrue(self.verified.exists())
        self.assertEqual(requests, ['/health'])

    def test_complete_restart_uses_installed_port_and_verifier(self):
        with endpoint() as (port, requests):
            result, _ = self.probe(port, complete=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.verified.exists())
        self.assertEqual(requests, ['/health'])


if __name__ == '__main__':
    unittest.main()
