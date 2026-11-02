"""Real loopback HTTP bodies obey response memory and monotonic read budgets."""
import contextlib
import http.client
import http.server
import json
import threading
import time
import unittest

if __package__:
    from . import test_source_broker as fixtures
else:
    import test_source_broker as fixtures

BROKER = fixtures.BROKER


@contextlib.contextmanager
def response_server(body, *, delay=0, chunk=64, declared='actual'):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            if declared != 'absent':
                self.send_header('Content-Length', str(len(body) if declared == 'actual' else len(body) + 10))
            self.end_headers()
            try:
                for start in range(0, len(body), chunk):
                    self.wfile.write(body[start:start + chunk])
                    self.wfile.flush()
                    if delay:
                        time.sleep(delay)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:%d/' % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


class SourceHttpBudgetTests(unittest.TestCase):
    def test_valid_short_chunks_are_accumulated_to_the_exact_json_document(self):
        value = {'ok': True, 'fact': 'Synthetic loopback fixture.'}
        body = json.dumps(value).encode()
        for declared in ('actual', 'absent'):
            with self.subTest(declared=declared), response_server(body, chunk=2, declared=declared) as url:
                self.assertEqual(BROKER.request_json(url, timeout=1, maximum=4096), value)

    def test_a_slow_drip_cannot_extend_each_socket_timeout_into_a_whole_body_wait(self):
        body = json.dumps({'ok': True, 'padding': 'x' * 30}).encode()
        with response_server(body, delay=.02, chunk=1) as url:
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                BROKER.request_json(url, timeout=.08, maximum=4096)
            # Allows scheduler headroom and one residual socket wait; this is
            # not an assertion of an exact .08s hard process deadline.
            self.assertLess(time.monotonic() - started, .8)

    def test_truncated_body_does_not_turn_a_valid_json_prefix_into_success(self):
        with response_server(b'{"ok":true}', declared='longer') as url:
            with self.assertRaises(http.client.IncompleteRead):
                BROKER.request_json(url, timeout=1, maximum=4096)

    def test_declared_and_streamed_oversize_responses_fail_closed(self):
        body = json.dumps({'padding': 'x' * 4096}).encode()
        for declared in ('actual', 'absent'):
            with self.subTest(declared=declared), response_server(body, declared=declared) as url:
                with self.assertRaises(BROKER.SourceUnavailable):
                    BROKER.request_json(url, timeout=1, maximum=1024)


if __name__ == '__main__':
    unittest.main()
