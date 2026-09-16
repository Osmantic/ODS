"""Bounded host-agent client; no privileged code is loaded from the checkout."""
import hashlib
import json
import socket
from pathlib import Path


def request_access(operation, request=None, *, settings_data_dir=None):
    if operation not in ("status", "change", "settings-status", "settings-change", "provider-status", "provider-change"):
        raise ValueError("invalid access operation")
    payload = {"operation": operation}
    if operation in ("change", "settings-change", "provider-change"): payload["request"] = request
    if operation.startswith(("settings-", "provider-")):
        # Supplied by the host agent's actual DATA_DIR, never the HTTP request.
        if settings_data_dir is None or not Path(settings_data_dir).is_absolute():
            raise ValueError("unqualified settings data directory")
        payload["data_dir_id"] = hashlib.sha256(str(Path(settings_data_dir)).encode("utf-8")).hexdigest()
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
