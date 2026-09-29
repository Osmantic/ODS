#!/usr/bin/env python3
"""Small authenticated OpenAI-compatible upstream for disposable install tests."""

import argparse
import http.server
import json
import os
import stat


def read_private_key(path: str) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("mock key must be an owner-owned regular file")
        if stat.S_IMODE(info.st_mode) not in (0o400, 0o600):
            raise ValueError("mock key must have mode 0400 or 0600")
        key = stream.read(4097).removesuffix(b"\n")
    if not 0 < len(key) <= 4096 or any(byte < 33 or byte > 126 for byte in key):
        raise ValueError("mock key must be one printable ASCII line")
    return key.decode("ascii")


class Handler(http.server.BaseHTTPRequestHandler):
    key = ""
    model = "ods-acceptance-mock"

    def log_message(self, format_string, *args):
        # BaseHTTPRequestHandler logs only request paths and status, never headers.
        super().log_message(format_string, *args)

    def reply(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        return self.headers.get("Authorization") == "Bearer " + self.key

    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, {"status": "ok"})
        if self.path != "/v1/models":
            return self.reply(404, {"error": {"message": "not found"}})
        if not self.authorized():
            return self.reply(401, {"error": {"message": "unauthorized"}})
        return self.reply(200, {
            "object": "list",
            "data": [{"id": self.model, "object": "model", "owned_by": "ods-test"}],
        })

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self.reply(404, {"error": {"message": "not found"}})
        if not self.authorized():
            return self.reply(401, {"error": {"message": "unauthorized"}})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 65536:
                raise ValueError("invalid body size")
            request = json.loads(self.rfile.read(size))
        except (ValueError, json.JSONDecodeError):
            return self.reply(400, {"error": {"message": "invalid request"}})
        if request.get("model") != self.model or not isinstance(request.get("messages"), list):
            return self.reply(400, {"error": {"message": "wrong model or messages"}})
        return self.reply(200, {
            "id": "chatcmpl-ods-acceptance",
            "object": "chat.completion",
            "created": 0,
            "model": self.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "OK"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--port", type=int, default=18080)
    arguments = parser.parse_args()
    Handler.key = read_private_key(arguments.key_file)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", arguments.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
