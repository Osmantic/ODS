"""Isolated Prometheus-compatible fixture; verifies stored source credentials."""

import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os

expected = "Basic " + base64.b64encode(("ods:" + os.environ["ODS_METRICS_PASSWORD"]).encode()).decode()
requests = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        if self.path == "/seen":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(requests).encode())
            return
        self.respond()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.respond()

    def respond(self):
        if self.headers.get("Authorization") != expected:
            self.send_response(401)
            self.end_headers()
            return
        requests.append(self.path)
        payload = {"status": "success", "data": {"resultType": "matrix", "result": [{
            "metric": {"__name__": "ods_fixture", "instance": "local-fixture"},
            "values": [[1740000000, "3.14"], [1740000060, "3.14"]],
        }]}}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())


HTTPServer(("127.0.0.1", 18080), Handler).serve_forever()
