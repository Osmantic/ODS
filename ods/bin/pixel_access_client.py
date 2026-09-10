"""Bounded host-agent client; no privileged code is loaded from the checkout."""
import json
import socket


def request_access(operation, request=None):
    payload = {"operation": operation}
    if operation == "change": payload["request"] = request
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(335)
        connection.connect("/run/ods-pixel-access/control.sock")
        connection.sendall(json.dumps(payload).encode() + b"\n")
        with connection.makefile("rb") as stream:
            raw = stream.readline(65537)
        if len(raw) > 65536 or not raw.endswith(b"\n"): raise ValueError("invalid access response")
        value = json.loads(raw)
        if set(value) != {"status", "body"} or value["status"] not in (200, 400, 403, 409, 503) or not isinstance(value["body"], dict):
            raise ValueError("invalid access response")
        return value["status"], value["body"]
