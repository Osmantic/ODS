#!/usr/bin/env python3
import argparse
import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_SHUTDOWN = threading.Event()


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_):
        pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--rpc", default="")
    args, _ = p.parse_known_args()

    log = {
        "pid": os.getpid(),
        "rpc": args.rpc,
        "argv": sys.argv[1:],
    }
    log_path = os.environ.get("FAKE_LLAMA_LOG", "/tmp/fake-llama.log")
    with open(log_path, "a") as f:
        f.write(json.dumps(log) + "\n")

    signal.signal(signal.SIGTERM, lambda *_: _SHUTDOWN.set())
    signal.signal(signal.SIGINT, lambda *_: _SHUTDOWN.set())

    srv = HTTPServer((args.host, args.port), H)
    srv.timeout = 0.5

    while not _SHUTDOWN.is_set():
        srv.handle_request()

    srv.server_close()
    sys.exit(0)


if __name__ == "__main__":
    main()
