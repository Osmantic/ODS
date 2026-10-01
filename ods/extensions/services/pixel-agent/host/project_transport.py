"""Authenticated local transport for the candidate project controller.

The installer/service must provide the controller's policy adapter and owner
identity. No socket listener, permission change or default allow policy here.
"""
import hashlib
import json
import re
import subprocess

from project_dispatch import dispatch_project
from unix_peer import peer_ids
from project_runtime import verify_runtime

MAX_REQUEST_BYTES = 8192
MAX_RESPONSE_BYTES = 1024 * 1024


def serve_project_connection(connection, *, controller, owner_uid):
    try:
        connection.settimeout(10)
        uid, _ = peer_ids(connection)
        if uid != owner_uid:
            raise PermissionError("owner connection required")
        payload = bytearray()
        while len(payload) <= MAX_REQUEST_BYTES:
            piece = connection.recv(min(1024, MAX_REQUEST_BYTES + 1 - len(payload)))
            if not piece:
                break
            payload.extend(piece)
            if b"\n" in piece:
                break
        if (len(payload) > MAX_REQUEST_BYTES or payload.count(b"\n") != 1
                or not payload.endswith(b"\n")):
            raise ValueError("invalid framing")
        envelope = json.loads(payload)
        if envelope == {"schemaVersion": 1, "action": "health"}:
            verify_runtime(controller.image)
            response = {"schemaVersion": 1, "kind": "ods-project-runtime", "status": "ready",
                        "image": controller.image, "executionPolicy": "runtime-verified-full-access"}
            if controller.python_image:
                verify_runtime(controller.python_image, runtime="python")
                response["runtimes"] = {"npm": controller.image, "python": controller.python_image}
            connection.sendall(json.dumps(response).encode() + b"\n")
            return
        if not isinstance(envelope, dict) or set(envelope) != {"context", "request"}:
            raise ValueError("invalid envelope")
        context = envelope["context"]
        if not isinstance(context, dict) or set(context) != {"sessionId", "toolCallId"}:
            raise ValueError("runtime context required")
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,192}", value)
               for value in context.values()):
            raise ValueError("invalid runtime identity")
        # Bind replay to the kernel-authenticated owner plus runtime call context.
        # This key is only idempotency, never execution authority.
        replay = hashlib.sha256(json.dumps([uid, context["sessionId"], context["toolCallId"]],
                                          separators=(",", ":")).encode()).hexdigest()
        result = dispatch_project(controller, envelope["request"], request_key=replay)
    except PermissionError:
        result = {"schemaVersion": 1, "kind": "ods-project-job", "status": "denied"}
    except (ValueError, TypeError, UnicodeError):
        result = {"schemaVersion": 1, "kind": "ods-project-job", "status": "invalid-request"}
    except (OSError, KeyError, RuntimeError, subprocess.SubprocessError):
        result = {"schemaVersion": 1, "kind": "ods-project-job", "status": "unconfirmed"}
    response = json.dumps(result, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    if len(response) > MAX_RESPONSE_BYTES:
        response = b'{"schemaVersion":1,"kind":"ods-project-job","status":"unconfirmed"}\n'
    connection.sendall(response)
