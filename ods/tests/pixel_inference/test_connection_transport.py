"""Real loopback requests; never contact public or fleet endpoints."""
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler,HTTPServer
import json
import socket
import threading
import time
from urllib.parse import urlsplit

import pytest

from test_connection import BASE_CONN,PROBE_ROOT
from pixel_provider import connection_transport as mod


@contextmanager
def server(status=200,body=None):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_GET(self):
            calls.append((self.path,dict(self.headers)))
            self.send_response(status)
            raw = body if body is not None else json.dumps(probe).encode()
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Location','http://127.0.0.1:1/never-follow')
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError):
                pass
    service = HTTPServer(('127.0.0.1',0),Handler)
    conn,probe = deepcopy(BASE_CONN),deepcopy(PROBE_ROOT)
    conn['baseUrl'] = f'http://127.0.0.1:{service.server_port}/v1'
    conn['expiresAt'] = probe['ods']['expiresAt'] = int(time.time())+3600
    thread = threading.Thread(target=service.serve_forever,daemon=True)
    thread.start()
    try:
        yield conn,calls
    finally:
        service.shutdown(); thread.join(timeout=3); service.server_close()


def test_probe_success_ignores_proxy_environment(monkeypatch):
    monkeypatch.setenv('HTTP_PROXY','http://127.0.0.1:1')
    with server() as (conn,calls):
        result = mod.probe_connection(conn,confirmed_endpoint=conn['baseUrl'])
    assert result['routedModel'] == 'GLM'
    assert len(calls) == 1 and calls[0][0] == '/v1/models'
    assert calls[0][1]['Authorization'] == 'Bearer '+conn['credential']['apiKey']


@pytest.mark.parametrize('status,code',[(401,'connection-denied'),(403,'connection-denied'),
    (301,'connection-unavailable'),(500,'connection-unavailable')])
def test_denial_or_redirect_single_request(status,code):
    with server(status) as (conn,calls):
        with pytest.raises(mod.StoreError,match=code) as error:
            mod.probe_connection(conn,confirmed_endpoint=conn['baseUrl'])
        assert conn['credential']['apiKey'] not in str(error.value)
    assert len(calls) == 1


def test_oversized_response():
    with server(body=b'x'*(mod.MAX_BYTES+1)) as (conn,calls):
        with pytest.raises(mod.StoreError,match='invalid-probe'):
            mod.probe_connection(conn,confirmed_endpoint=conn['baseUrl'])
    assert len(calls) == 1


def test_mismatch_before_any_request():
    with server() as (conn,calls):
        with pytest.raises(mod.StoreError,match='connection-endpoint-not-confirmed'):
            mod.probe_connection(conn,confirmed_endpoint='http://127.0.0.1:1/v1')
    assert calls == []


@pytest.mark.parametrize('address',['0.0.0.0','224.0.0.1','169.254.169.254','::ffff:169.254.169.254'])
def test_forbidden_dns_answer(address,monkeypatch):
    monkeypatch.setattr(mod.socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))])
    with pytest.raises(mod.StoreError,match='unsafe-connection-address'):
        mod._target(urlsplit('https://confirmed.example/v1'))
