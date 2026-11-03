"""A stand-in service for the real-process supervisor tests.

usage: health_child.py <port> <mode> [marker]
  serve        answer GET /health with 200 until killed
  crash-once   first run (marker absent): create marker, serve /health, exit 3 after 1s;
               later runs: serve until killed
  exit         exit 2 at once without serving
"""
import http.server
import os
import sys
import threading

port, mode = int(sys.argv[1]), sys.argv[2]
print(f"health_child pid={os.getpid()} mode={mode} port={port}", flush=True)
if mode == "exit":
    sys.exit(2)


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def log_message(self, *_args):
        pass


server = http.server.HTTPServer(("127.0.0.1", port), Health)
if mode == "crash-once" and not os.path.exists(sys.argv[3]):
    open(sys.argv[3], "w").close()
    print("health_child will crash", flush=True)
    threading.Timer(1.0, lambda: os._exit(3)).start()
print("health_child serving", flush=True)
server.serve_forever()
