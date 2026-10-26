"""Bounded host-agent client; no privileged code is loaded from the checkout."""
import hashlib
import json
import socket
import struct
import sys
from pathlib import Path


ACCESS_SOCKET_PATH = ("/private/var/run/ods-pixel-access/control.sock"
                      if sys.platform == "darwin" else "/run/ods-pixel-access/control.sock")


def request_access(operation, request=None, *, settings_data_dir=None):
    if operation not in ("status", "change", "model-status", "model-begin", "model-finish", "installer-model-verify",
                         "installer-release-prepare", "installer-release-publish", "installer-release-finish", "installer-release-abort", "installer-source-begin",
                          "settings-status", "settings-change", "provider-status", "provider-change",
                          "connection-status", "connection-begin", "connection-complete", "connection-activate",
                          "connection-cancel", "connection-disconnect", "connection-sync",
                          "connection-proposals", "connection-approve", "connection-decline",
                          "connection-reconcile", "connection-activity", "connection-undo", "connection-drafts",
                          "connection-plugin", "schedules"):
        raise ValueError("invalid access operation")
    payload = {"operation": operation}
    if (operation.startswith("installer-") or operation in ("change", "model-finish", "settings-change", "provider-change", "schedules")
            or operation.startswith("connection-") and operation != "connection-status"):
        payload["request"] = request
    if operation.startswith(("settings-", "provider-")):
        # Supplied by the host agent's actual DATA_DIR, never the HTTP request.
        if settings_data_dir is None or not Path(settings_data_dir).is_absolute():
            raise ValueError("unqualified settings data directory")
        payload["data_dir_id"] = hashlib.sha256(str(Path(settings_data_dir)).encode("utf-8")).hexdigest()
    if operation == 'schedules':
        if settings_data_dir is None or not Path(settings_data_dir).is_absolute():
            raise ValueError('unqualified schedule data directory')
        payload['source'] = {'kind': 'data-directory', 'identity': hashlib.sha256(str(Path(settings_data_dir)).encode()).hexdigest()}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1850 if operation in ("model-begin", "installer-source-begin") else 335)
        connection.connect(ACCESS_SOCKET_PATH)
        if operation.startswith("installer-"):
            # This response is an authority input to the Linux installer, not
            # merely a UI status. Authenticate the root peer before sending.
            if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
                raise ValueError("installer verification requires Linux peer credentials")
            credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            if struct.unpack("3i", credentials)[1] != 0:
                raise ValueError("installer verification requires root coordinator")
        connection.sendall(json.dumps(payload).encode() + b"\n")
        with connection.makefile("rb") as stream:
            maximum = 525312 if operation == 'schedules' else 65536
            raw = stream.readline(maximum + 1)
        if len(raw) > maximum or not raw.endswith(b"\n"): raise ValueError("invalid access response")
        value = json.loads(raw)
        if set(value) != {"status", "body"} or value["status"] not in (200, 400, 403, 409, 503) or not isinstance(value["body"], dict):
            raise ValueError("invalid access response")
        return value["status"], value["body"]
