#!/usr/bin/env python3
"""End-to-end check of the native dashboard: no Docker, no nginx.

Starts a fake llama-server that requires its API key (as ``--api-key-file``
does), builds the dashboard (``npm ci && npm run build``), runs dashboard-api
with ``ODS_RUNTIME=native`` and the native chat proxy on 127.0.0.1, and checks:

- ``/`` serves the dashboard's index.html;
- ``/api/status`` through the sign-in gate reports llama-server healthy with
  its model, and ``/api/chat`` returns the model's text;
- a key-less request that does not pass the gate is 401, so are a forged Host
  and forwarding headers; a cross-site POST is 403;
- the chat proxy answers a chat with the key added server-side, streams it,
  and refuses untrusted and cross-site requests;
- neither key appears in any served asset or response.

Runs on Linux and Windows with the dashboard-api Python (stdlib only here):

    python ods/installers/windows/native/tests/e2e_dashboard_native.py [--skip-build]
"""

from __future__ import annotations

import argparse
import http.client
import http.server
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ODS = Path(__file__).resolve().parents[4]
API_DIR = ODS / "extensions" / "services" / "dashboard-api"
DASHBOARD_DIR = ODS / "extensions" / "services" / "dashboard"
MODEL = "native-e2e-model.gguf"
LOOPBACK = "127.0.0.1"


# --- fake llama-server ------------------------------------------------------------

class FakeLlama(http.server.BaseHTTPRequestHandler):
    """b9014's routes as the dashboard and web UI use them; API routes need the key."""
    api_key = ""
    public = {"/health", "/v1/health", "/models", "/v1/models", "/", "/index.html", "/bundle.js", "/bundle.css"}

    def log_message(self, *_args):
        pass

    def _send(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # llama-server reflects Origin; the proxy must drop it.
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", ""))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        path = self.path.split("?", 1)[0]
        if path in self.public or self.headers.get("Authorization") == f"Bearer {self.api_key}":
            return True
        self._send(401, {"error": {"message": "Invalid API Key", "type": "authentication_error", "code": 401}})
        return False

    def do_GET(self):
        if not self._authorized():
            return
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/v1/health"):
            self._send(200, {"status": "ok"})
        elif path in ("/models", "/v1/models"):
            self._send(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        elif path == "/props":
            self._send(200, {"default_generation_settings": {"n_ctx": 8192}, "model_alias": MODEL,
                             "modalities": {"vision": False}})
        elif path == "/metrics":
            self._send(200, b"llamacpp:tokens_predicted_total 0\nllamacpp:tokens_predicted_seconds_total 0\n",
                       "text/plain")
        elif path == "/":
            self._send(200, b"<!doctype html><title>llama.cpp</title><script>fetch('./props')</script>",
                       "text/html; charset=utf-8")
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return
        length = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        if self.path != "/v1/chat/completions":
            return self._send(404, {"error": "not found"})
        if not request.get("stream"):
            return self._send(200, {"choices": [{"message": {"role": "assistant", "content": "hello from native"}}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for word in ("hello", " from", " stream"):
            self.wfile.write(f'data: {{"choices":[{{"delta":{{"content":"{word}"}}}}]}}\n\n'.encode())
            self.wfile.flush()
            time.sleep(0.3)
        self.wfile.write(b"data: [DONE]\n\n")


def start_fake_llama(port, api_key):
    FakeLlama.api_key = api_key
    server = http.server.ThreadingHTTPServer((LOOPBACK, port), FakeLlama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# --- helpers ------------------------------------------------------------------------

def free_port():
    with socket.socket() as sock:
        sock.bind((LOOPBACK, 0))
        return sock.getsockname()[1]


def request(port, method, path, headers=None, body=None, host=None, timeout=60):
    connection = http.client.HTTPConnection(LOOPBACK, port, timeout=timeout)
    headers = dict(headers or {})
    connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    connection.putheader("Host", host or f"{LOOPBACK}:{port}")
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers.setdefault("Content-Type", "application/json")
        headers["Content-Length"] = str(len(data))
    for name, value in headers.items():
        connection.putheader(name, value)
    connection.endheaders(data)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response.status, dict(response.getheaders()), payload


def wait_healthy(port, deadline_seconds=60):
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            if request(port, "GET", "/health", timeout=2)[0] == 200:
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"nothing became healthy on port {port}")


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok   {message}")


def build_dashboard():
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        raise RuntimeError("npm is required to build the dashboard")
    for command in ([npm, "ci", "--no-audit", "--no-fund"], [npm, "run", "build"]):
        subprocess.run(command, cwd=DASHBOARD_DIR, check=True)


def start_uvicorn(module, port, env, log):
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", module, "--host", LOOPBACK, "--port", str(port)],
        cwd=API_DIR, env=env, stdout=log, stderr=subprocess.STDOUT,
    )


# --- the checks ---------------------------------------------------------------------

def check_dashboard(api_port, dashboard_key, llama_key, dist):
    secrets_in = (dashboard_key.encode(), llama_key.encode())

    def no_secret(status_headers_body, what):
        _, headers, body = status_headers_body
        blob = body + json.dumps(headers).encode()
        check(not any(secret in blob for secret in secrets_in), f"no key in {what}")

    index = request(api_port, "GET", "/")
    check(index[0] == 200 and b"<div id=\"root\">" in index[2], "/ serves the dashboard index.html")
    check("default-src 'self'" in index[1].get("content-security-policy", ""), "/ carries the nginx CSP")
    no_secret(index, "index.html")
    assets = re.findall(rb'(?:src|href)="(/assets/[^"]+)"', index[2])
    check(assets, "index.html references built assets")
    for asset in assets:
        no_secret(request(api_port, "GET", asset.decode()), asset.decode())
    for path in sorted(p for p in dist.rglob("*") if p.is_file()):
        data = path.read_bytes()
        check(not any(secret in data for secret in secrets_in), f"no key in dist/{path.relative_to(dist).as_posix()}")
    no_secret(request(api_port, "GET", "/models"), "SPA fallback")

    deadline = time.monotonic() + 30
    while True:
        status = request(api_port, "GET", "/api/status")
        services = {s["id"]: s["status"] for s in json.loads(status[2]).get("services", [])} if status[0] == 200 else {}
        if services.get("llama-server") == "healthy" or time.monotonic() > deadline:
            break
        time.sleep(1)
    body = json.loads(status[2])
    check(status[0] == 200, "/api/status passes the gate for a local browser")
    check(services == {"llama-server": "healthy", "dashboard-api": "healthy"},
          f"status lists only the enabled services, all healthy ({services})")
    check(body["loadedModel"] == MODEL, f"status reports the loaded model ({body['loadedModel']})")
    check(body["runtime"]["kind"] == "native" and "pixel" in body["runtime"]["unavailable"],
          "status reports the native runtime and its unavailable features")
    no_secret(status, "/api/status")

    readiness = json.loads(request(api_port, "GET", "/api/readiness")[2])
    check(readiness["ready"] and [c["id"] for c in readiness["checks"]] == ["chat", "dashboard-api"],
          "readiness covers exactly the enabled services and is ready")

    chat = request(api_port, "POST", "/api/chat", body={"message": "hi"},
                   headers={"Origin": f"http://{LOOPBACK}:{api_port}"})
    check(chat[0] == 200 and json.loads(chat[2])["response"] == "hello from native", "/api/chat returns the model's text")
    no_secret(chat, "/api/chat")

    links = json.loads(request(api_port, "GET", "/api/external-links")[2])
    check([link["id"] for link in links] == ["chat"], "the dashboard offers the native Chat entry")

    for label, kwargs in (
        ("non-loopback Host", {"host": f"ods.example:{api_port}"}),
        ("X-Forwarded-For", {"headers": {"X-Forwarded-For": "203.0.113.7"}}),
        ("Forwarded", {"headers": {"Forwarded": "for=203.0.113.7"}}),
    ):
        result = request(api_port, "GET", "/api/status", **kwargs)
        check(result[0] == 401 and result[1].get("x-ods-sign-in") == "required", f"{label} is 401 sign-in required")
    check(request(api_port, "GET", "/api/status", host=f"ods.example:{api_port}",
                  headers={"Authorization": "Bearer wrong"})[0] == 403, "a wrong direct key is refused")
    check(request(api_port, "GET", "/api/status", headers={"Authorization": f"Bearer {dashboard_key}"},
                  host=f"ods.example:{api_port}")[0] == 200, "a direct client with the key is served")
    cross = request(api_port, "POST", "/api/chat", body={"message": "hi"}, headers={"Origin": "http://evil.example"})
    check(cross[0] == 403, "a cross-site POST is 403")
    check(request(api_port, "GET", "/api/talk/status")[0] == 404, "Docker-only Talk is not offered")


def check_chat(chat_port, llama_port, llama_key, dashboard_key):
    check(request(llama_port, "POST", "/v1/chat/completions", body={"messages": []})[0] == 401,
          "llama-server itself refuses a key-less chat")
    page = request(chat_port, "GET", "/")
    check(page[0] == 200 and b"llama.cpp" in page[2], "the chat port serves llama-server's web UI")
    check("access-control-allow-origin" not in {k.lower() for k in page[1]}, "llama-server's CORS reflection is dropped")
    props = request(chat_port, "GET", "/props")
    check(props[0] == 200 and json.loads(props[2])["model_alias"] == MODEL, "the UI's ./props call gets the key added")
    origin = {"Origin": f"http://{LOOPBACK}:{chat_port}"}
    answer = request(chat_port, "POST", "/v1/chat/completions", body={"messages": []}, headers=origin)
    check(answer[0] == 200 and b"hello from native" in answer[2], "chat through the proxy answers")

    connection = http.client.HTTPConnection(LOOPBACK, chat_port, timeout=30)
    data = json.dumps({"messages": [], "stream": True}).encode()
    connection.request("POST", "/v1/chat/completions", body=data,
                       headers={"Content-Type": "application/json", **origin})
    response = connection.getresponse()
    started = time.monotonic()
    first = response.readline()
    first_at = time.monotonic() - started
    rest = response.read()
    total = time.monotonic() - started
    connection.close()
    check(first.startswith(b"data: ") and b"hello" in first, "the stream's first event arrives")
    check(first_at < 0.25 and total > 0.5, f"streaming is not buffered (first {first_at:.2f}s, all {total:.2f}s)")
    check(b"[DONE]" in rest, "the stream completes")
    for result, what in ((page, "chat UI"), (props, "./props"), (answer, "chat answer")):
        blob = result[2] + json.dumps(result[1]).encode()
        check(llama_key.encode() not in blob and dashboard_key.encode() not in blob, f"no key in {what}")

    check(request(chat_port, "GET", "/props", host=f"ods.example:{chat_port}")[0] == 401,
          "untrusted Host on the chat port is 401")
    check(request(chat_port, "GET", "/props", headers={"X-Forwarded-For": "203.0.113.7"})[0] == 401,
          "forwarding headers on the chat port are 401")
    check(request(chat_port, "POST", "/v1/chat/completions", body={},
                  headers={"Origin": "http://evil.example"})[0] == 403, "a cross-site chat POST is 403")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-build", action="store_true", help="use the existing dashboard dist/")
    args = parser.parse_args()
    if not args.skip_build:
        build_dashboard()
    dist = DASHBOARD_DIR / "dist"
    if not (dist / "index.html").is_file():
        raise RuntimeError(f"no dashboard build at {dist}")

    work = Path(tempfile.mkdtemp(prefix="ods-native-e2e-"))
    install_dir, data_dir = work / "install", work / "data"
    for directory in (install_dir, data_dir):
        directory.mkdir()
    (install_dir / ".env").write_text(f"ODS_MODE=local\nGGUF_FILE={MODEL}\nLLM_MODEL={MODEL}\n", encoding="utf-8")
    llama_key, dashboard_key = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    llama_port, api_port, chat_port = free_port(), free_port(), free_port()

    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("ODS_", "DASHBOARD_", "LLAMA_", "OLLAMA_", "LLM_"))}
    # The supervisor's environment, plus what the native mode adds. Manifests
    # and the dashboard build are found next to dashboard-api, as in the bundle.
    env.update({
        "ODS_RUNTIME": "native",  # the default on Windows
        "ODS_INSTALL_DIR": str(install_dir),
        "ODS_DATA_DIR": str(data_dir),
        "ODS_MODE": "local",
        "GPU_BACKEND": "cpu",
        "DASHBOARD_API_KEY": dashboard_key,
        "DASHBOARD_API_PORT": str(api_port),
        "LLAMA_SERVER_PORT": str(llama_port),
        # The supervisor passes the key's value; LLAMA_SERVER_API_KEY_FILE also works.
        "LLAMA_SERVER_API_KEY": llama_key,
        "ODS_NATIVE_CHAT_PORT": str(chat_port),
        "PYTHONUNBUFFERED": "1",
    })
    fake = start_fake_llama(llama_port, llama_key)
    log_path = work / "processes.log"
    processes = []
    try:
        with open(log_path, "wb") as log:
            processes.append(start_uvicorn("main:app", api_port, env, log))
            processes.append(start_uvicorn("native_chat:app", chat_port, env, log))
            wait_healthy(api_port)
            check_dashboard(api_port, dashboard_key, llama_key, dist)
            check_chat(chat_port, llama_port, llama_key, dashboard_key)
        print("PASS native dashboard end-to-end")
    except BaseException:
        sys.stdout.write(log_path.read_text(encoding="utf-8", errors="replace")[-6000:])
        raise
    finally:
        for process in processes:
            process.terminate()
            process.wait(timeout=20)
        fake.shutdown()
        shutil.rmtree(work, ignore_errors=True)  # temporary test state only


if __name__ == "__main__":
    main()
