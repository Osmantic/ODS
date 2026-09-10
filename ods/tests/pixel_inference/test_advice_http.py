"""Cancellation must close real upstream TCP, not merely an in-memory mock."""
import asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import select
import socket
import threading

import pytest

from test_advice import saved,request
from pixel_provider.advice import AdvisoryCall
from pixel_provider.vault import CredentialStore


def test_cancel_closes_real_upstream_connection(saved):
    entered=threading.Event(); disconnected=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            entered.set()
            readable,_,_=select.select([self.connection],[],[],3)
            if readable and self.connection.recv(1,socket.MSG_PEEK)==b'':
                disconnected.set()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
    root,config=saved
    config['providers'][1]['baseUrl']=f'http://127.0.0.1:{server.server_port}/v1'
    CredentialStore(root).save_public(dict(expectedRevision=1,document=config,
        credentialChanges={'backup':dict(action='set',value='fixture-advisor-key')}))
    try:
        call=AdvisoryCall(root,request(expectedRevision=2))
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(call.execute(cancelled=entered.is_set))
        assert entered.is_set() and disconnected.wait(1), 'Stop did not close the upstream TCP request'
    finally:
        server.shutdown(); server.server_close(); worker.join(3)
