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

    def completion(self, request: dict, message: dict, finish: str):
        if request.get("stream") is True:
            delta = dict(message)
            if "tool_calls" in delta:
                delta["tool_calls"] = [
                    {"index": index, **call}
                    for index, call in enumerate(delta["tool_calls"])
                ]
            events = [
                {"id": "chatcmpl-ods-acceptance", "object": "chat.completion.chunk",
                 "created": 0, "model": self.model,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"id": "chatcmpl-ods-acceptance", "object": "chat.completion.chunk",
                 "created": 0, "model": self.model,
                 "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
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
            "id": "chatcmpl-ods-acceptance", "object": "chat.completion",
            "created": 0, "model": self.model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

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
        probe = any(
            message.get("role") == "user"
            and "ODS_SEARCH_PROBE" in json.dumps(message)
            for message in request["messages"] if isinstance(message, dict)
        )
        marker_anywhere = "ODS_SEARCH_PROBE" in json.dumps(request["messages"])
        if marker_anywhere or any(
            isinstance(message, dict) and message.get("role") == "tool"
            for message in request["messages"]
        ):
            tool_names = [item.get("function", {}).get("name")
                          for item in (request.get("tools") or [])
                          if isinstance(item, dict) and item.get("type") == "function"
                          and isinstance(item.get("function"), dict)]
            tool_messages = [item for item in request["messages"]
                             if isinstance(item, dict) and item.get("role") == "tool"]
            self.log_message(
                "search_probe_shape marker_user=%s marker_anywhere=%s roles=%s "
                "web_search_offered=%s tool_messages=%d matching_tool_result=%s",
                probe, marker_anywhere,
                [item.get("role") for item in request["messages"][-12:]
                 if isinstance(item, dict)],
                "web_search" in tool_names, len(tool_messages),
                any(item.get("tool_call_id") == "call_ods_web_search"
                    for item in tool_messages),
            )
        if probe:
            tool_results = [message for message in request["messages"]
                            if isinstance(message, dict) and message.get("role") == "tool"
                            and message.get("tool_call_id") == "call_ods_web_search"]
            if tool_results:
                content = tool_results[-1].get("content")
                rendered = content if isinstance(content, str) else json.dumps(content)
                has_url = "http://" in rendered or "https://" in rendered
                self.log_message("search_tool_result_seen=%s bytes=%d", has_url, len(rendered))
                final = "ODS_SEARCH_TOOL_RESULT_SEEN" if has_url else "ODS_SEARCH_TOOL_RESULT_EMPTY"
                return self.completion(request, {"role": "assistant", "content": final}, "stop")
            web_tool = next((item for item in (request.get("tools") or [])
                             if isinstance(item, dict) and item.get("type") == "function"
                             and item.get("function", {}).get("name") == "web_search"), None)
            if web_tool is None:
                self.log_message("search_probe_missing_web_search_tool")
                return self.reply(422, {"error": {"message": "web_search not offered"}})
            arguments = json.dumps({"query": "OpenAI official website"}, separators=(",", ":"))
            call = {"id": "call_ods_web_search", "type": "function",
                    "function": {"name": "web_search", "arguments": arguments}}
            self.log_message("search_probe_issued_web_search")
            return self.completion(request, {"role": "assistant", "tool_calls": [call]}, "tool_calls")
        return self.completion(request, {"role": "assistant", "content": "OK"}, "stop")


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
