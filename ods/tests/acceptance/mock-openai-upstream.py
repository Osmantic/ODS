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
            if not 0 < size <= 4 * 1024 * 1024:
                self.log_message("reject=body-size bytes=%d", size)
                raise ValueError("invalid body size")
            request = json.loads(self.rfile.read(size))
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
        except (ValueError, json.JSONDecodeError):
            self.log_message("reject=request-json")
            return self.reply(400, {"error": {"message": "invalid request"}})
        if request.get("model") != self.model or not isinstance(request.get("messages"), list):
            self.log_message("reject=model-or-messages bytes=%d model_match=%s messages_list=%s",
                             size, request.get("model") == self.model,
                             isinstance(request.get("messages"), list))
            return self.reply(400, {"error": {"message": "wrong model or messages"}})
        self.log_message("accept=chat bytes=%d messages=%d stream=%s",
                         size, len(request["messages"]), request.get("stream") is True)
        structured = isinstance(request.get("response_format"), dict)
        web_case = any(
            "ODS acceptance web query" in str(message.get("content", ""))
            for message in request["messages"] if isinstance(message, dict)
        )
        content = "OK"
        if structured:
            # Vane's first model call classifies the question with a JSON schema.
            # A plain answer makes its async search fail while /api/search waits.
            content = json.dumps({
                "classification": {
                    "skipSearch": not web_case,
                    "personalSearch": False,
                    "academicSearch": False,
                    "discussionSearch": False,
                    "showWeatherWidget": False,
                    "showStockWidget": False,
                    "showCalculationWidget": False,
                },
                "standaloneFollowUp": "ODS acceptance model route check",
            })
        self.log_message("accept=chat-kind structured=%s", structured)
        if request.get("stream") is True:
            # This Hermes route journey checks model delivery, not web-tool
            # execution; return a plain answer even when Hermes sends tools.
            has_tools = False
            previous_tool_result = any(
                isinstance(message, dict) and message.get("role") == "tool"
                for message in request["messages"]
            )
            if has_tools and not previous_tool_result:
                self.log_message("accept=tool-web")
                first_delta = {"role": "assistant", "tool_calls": [{
                    "index": 0, "id": "call-ods-acceptance", "type": "function",
                    "function": {"name": "web_search", "arguments": json.dumps({
                        "type": "web_search", "queries": ["Python programming language"],
                    })},
                }]}
                finish_reason = "tool_calls"
            else:
                first_delta = {"role": "assistant", "content": "OK"}
                finish_reason = "stop"
            events = [
                {"id": "chatcmpl-ods-acceptance", "object": "chat.completion.chunk",
                 "created": 0, "model": self.model,
                 "choices": [{"index": 0, "delta": first_delta, "finish_reason": None}]},
                {"id": "chatcmpl-ods-acceptance", "object": "chat.completion.chunk",
                 "created": 0, "model": self.model,
                 "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]},
            ]
            body = ("".join("data: " + json.dumps(event) + "\n\n" for event in events)
                    + "data: [DONE]\n\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return self.reply(200, {
            "id": "chatcmpl-ods-acceptance",
            "object": "chat.completion",
            "created": 0,
            "model": self.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
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
