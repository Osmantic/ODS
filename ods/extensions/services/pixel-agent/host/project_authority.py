"""Read-only adapter for existing, runtime-verified Full Access authority.

Never enables permissions or substitutes disk configuration for runtime proof.
Sandbox project grants are not implemented by this adapter. The outer project
transport must already authenticate the configured owner before invoking it.
"""
import json
import socket
import sys

from unix_peer import peer_ids

ACCESS_SOCKET = ("/private/var/run/ods-pixel-access/control.sock"
                 if sys.platform == "darwin" else "/run/ods-pixel-access/control.sock")


def read_verified_access():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(ACCESS_SOCKET)
        uid, _ = peer_ids(connection)
        if uid != 0:
            raise PermissionError("root access coordinator required")
        connection.sendall(b'{"operation":"status"}\n')
        with connection.makefile("rb") as stream:
            payload = stream.readline(65537)
        if len(payload) > 65536 or not payload.endswith(b"\n"):
            raise ValueError("invalid access response")
        result = json.loads(payload)
        if not isinstance(result, dict) or set(result) != {"status", "body"}:
            raise ValueError("invalid access envelope")
        if result["status"] != 200 or not isinstance(result["body"], dict):
            return False
        body = result["body"]
        return (body.get("available") is True and body.get("configured_mode") == "full-access"
                and body.get("effective_mode") == "full-access" and body.get("runtime_verified") is True)


class ManagedFullAccessPolicy:
    def __call__(self, project, action, binding=None):
        # The authenticated owner can always observe or request a stop, even
        # after revocation. These actions do not submit new execution.
        if action in ("observe", "cancel", "capabilities"):
            return True
        if action not in ("snapshot", "execute", "import"):
            return False
        try:
            return read_verified_access() is True
        except (OSError, ValueError, PermissionError):
            return False
