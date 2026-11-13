#!/usr/bin/env python3
"""Exercise the installer's actual owner-readiness probe against Unix sockets."""
from contextlib import contextmanager
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


def extract_probe(source):
    function = source.split('_ods_pixel_install_integrations() {\n', 1)[1]
    invocation = ('ods_pixel_run_as_owner "$owner" "$home" /usr/bin/python3 - '
                  '"$home/.config/ods/pixel-integrations/owner.sock" <<\'PY\' || return 1\n')
    if function.count(invocation) != 1:
        raise AssertionError('Expected exactly one integrations owner probe')
    return function.split(invocation, 1)[1].split('\nPY\n', 1)[0] + '\n'


PROBE = extract_probe((ROOT / 'installers/lib/pixel-host-install.sh').read_text())
REQUEST = b'{"schemaVersion":1,"action":"get"}\n'


@contextmanager
def listener(reply=b'{"ok":true}\n', *, delay=0, drip=False):
    """A real local peer; it never opens an ODS socket or reads owner state."""
    stopped = threading.Event()
    errors, requests = [], []
    with tempfile.TemporaryDirectory(prefix='ods-ready-') as directory:
        path = str(Path(directory) / 'owner.sock')

        def serve():
            try:
                if stopped.wait(delay):
                    return
                with socket.socket(socket.AF_UNIX) as server:
                    server.bind(path)
                    server.listen()
                    server.settimeout(.1)
                    while not stopped.is_set():
                        try:
                            client, _ = server.accept()
                        except TimeoutError:
                            continue
                        with client:
                            client.settimeout(.5)
                            raw = bytearray()
                            while b'\n' not in raw and len(raw) < 256:
                                piece = client.recv(256 - len(raw))
                                if not piece:
                                    break
                                raw.extend(piece)
                            requests.append(bytes(raw))
                            try:
                                if drip:
                                    for byte in reply:
                                        if stopped.wait(.04):
                                            break
                                        client.sendall(bytes([byte]))
                                else:
                                    client.sendall(reply)
                            except (BrokenPipeError, ConnectionResetError):
                                pass  # The deadline deliberately closes a slow peer.
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        try:
            yield path, requests
        finally:
            stopped.set()
            worker.join(2)
            if worker.is_alive():
                raise AssertionError('Readiness fixture failed to stop')
            if errors:
                raise errors[0]


class OwnerReadiness(unittest.TestCase):
    def probe(self, path, *, budget=None):
        code = PROBE
        if budget is not None:
            # Change only the fixed clock budget to test deadline failures
            # quickly; all production connect/read/retry logic stays exact.
            self.assertEqual(code.count('timeout_seconds = 60.0'), 1)
            code = code.replace('timeout_seconds = 60.0', f'timeout_seconds = {budget!r}')
        started = time.monotonic()
        result = subprocess.run([sys.executable, '-I', '-B', '-', path], input=code,
                                text=True, capture_output=True, timeout=70)
        return result, time.monotonic() - started

    def test_service_binds_after_former_ten_second_window(self):
        # Reproduces the real Type=simple failure: imports took ~12 seconds,
        # while the former 50 x .2 second loop quit before either socket bound.
        with listener(delay=11.2) as (path, requests):
            result, elapsed = self.probe(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(elapsed, 10.5)
        self.assertLess(elapsed, 25)
        self.assertEqual(requests, [REQUEST])

    def test_ready_peer_uses_exact_owner_get(self):
        with listener() as (path, requests):
            result, _ = self.probe(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(requests, [REQUEST])

    def test_absent_peer_has_bounded_deadline(self):
        with tempfile.TemporaryDirectory(prefix='ods-ready-') as directory:
            result, elapsed = self.probe(str(Path(directory) / 'missing.sock'), budget=.35)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 2)
        self.assertIn('last check: connection-unavailable', result.stderr)

    def test_non_ready_and_truthy_reply_never_pass(self):
        for reply in (b'{"ok":false}\n', b'{"ok":1}\n', b'[]\n'):
            with self.subTest(reply=reply), listener(reply) as (path, requests):
                result, elapsed = self.probe(path, budget=.35)
            self.assertNotEqual(result.returncode, 0)
            self.assertLess(elapsed, 2)
            self.assertIn('last check: not-ready', result.stderr)
            self.assertTrue(requests)
            self.assertTrue(all(request == REQUEST for request in requests))

    def test_malformed_and_unframed_replies_never_pass(self):
        for reply in (b'not-json\n', b'{"ok":true}', b'{"ok":true}\n{}\n'):
            with self.subTest(reply=reply), listener(reply) as (path, _):
                result, elapsed = self.probe(path, budget=.35)
            self.assertNotEqual(result.returncode, 0)
            self.assertLess(elapsed, 2)
            self.assertIn('last check: invalid-reply', result.stderr)

    def test_dripping_peer_cannot_extend_whole_deadline(self):
        with listener(b'{"ok":true,"padding":"' + b'x' * 50 + b'"}\n', drip=True) as (path, _):
            result, elapsed = self.probe(path, budget=.35)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 2)
        self.assertIn('last check: timeout', result.stderr)


if __name__ == '__main__':
    unittest.main()
