#!/usr/bin/env python3
"""Root-owned fixed-protocol service. The checkout is never an import path."""
import json
import os
from pathlib import Path
import pwd
import socket
import socketserver
import stat
import struct
import sys

sys.dont_write_bytecode = True


def protected(path):
    path = Path(path)
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError("program custody unavailable")


PROGRAM = Path(__file__).resolve().parent
protected(PROGRAM)
for name in ("access_mode_server.py", "pixel_access_bridge.py", "pixel_access_client.py", "pixel_access_reconcile.py",
             "access_mode_worker.py", "pixel_access_mode.py", "access_mode_config.py",
             "settings_transaction.py", "pixel_access_protocol.py", "pixel_settings/__init__.py",
             "pixel_settings/contract.py", "pixel_settings/projection.py", "pixel_settings/runtime.py", "pixel_settings/coordinator.py",
             "pixel_provider/__init__.py", "pixel_provider/config.py", "pixel_provider/store.py",
             "pixel_provider/activation_config.py", "pixel_provider/managed_deployment.py",
             "pixel_provider/service_environment.py", "pixel_provider/service_activation.py",
             "pixel_provider/runtime_custody.py", "pixel_provider/coordinator.py", "provider_transaction.py"):
    protected(PROGRAM / name)
sys.path.insert(0, str(PROGRAM))
from pixel_access_bridge import AccessError, SystemdAccessBridge, private_json
from pixel_access_protocol import control_request, decode_frame


def main():
    if os.geteuid() != 0: raise RuntimeError("root service required")
    settings = private_json("/etc/ods/pixel-access.json", 0, 8192)
    owner = pwd.getpwnam(settings["owner"])
    if owner.pw_uid == 0: raise RuntimeError("invalid owner")
    install = Path(settings["install_dir"])
    # Values remain private data. Never source .env or import owner code.
    values = {}
    for line in (install / ".env").read_text().splitlines():
        if line.startswith("DASHBOARD_API_KEY="):
            values["key"] = line.partition("=")[2].strip().strip("\"'")
    def make_adapter():
        # Discovery has request-local owner/gateway snapshots. Never let another
        # handler replace the active transition's authentication or runtime data.
        return SystemdAccessBridge(install, values.get("key", ""), installed_binary=settings["openclaw_bin"],
                                   gateway_owner=owner.pw_name, gateway_port=settings.get("gateway_port"),
                                   settings_data_dir=settings.get("settings_data_dir"))
    address = "/run/ods-pixel-access/control.sock"

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(340)
            _pid, uid, _gid = struct.unpack("3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            status, body = 403, {"error": "owner-required"}
            try:
                if uid not in (0, owner.pw_uid): raise PermissionError()
                raw = self.rfile.readline(2049)
                if len(raw) > 2048 or not raw.endswith(b"\n"): raise ValueError()
                request = control_request(decode_frame(raw.decode("utf-8"), 2048))
                adapter = make_adapter()
                if request == {"operation": "status"}:
                    status, body = 200, adapter.status()
                elif set(request) == {"operation", "request"} and request["operation"] == "change":
                    status, body = 200, adapter.change(request["request"])
                elif request["operation"].startswith("settings-"):
                    status = 200
                    body = (adapter.settings_status(data_dir_id=request["data_dir_id"])
                            if request["operation"] == "settings-status"
                            else adapter.change_settings(request["request"], data_dir_id=request["data_dir_id"]))
                elif request["operation"].startswith("provider-"):
                    status = 200
                    body = (adapter.provider_status(data_dir_id=request["data_dir_id"])
                            if request["operation"] == "provider-status"
                            else adapter.change_providers(request["request"], data_dir_id=request["data_dir_id"]))
                else: raise ValueError()
            except PermissionError: pass
            except AccessError as error: status, body = 409, {"error": error.code}
            except (ValueError, TypeError): status, body = 400, {"error": "invalid-request"}
            except Exception: status, body = 503, {"error": "access-service-unavailable"}
            self.wfile.write(json.dumps({"status": status, "body": body}).encode() + b"\n")

    # RuntimeDirectory is root-only until this daemon provisions the owner socket.
    os.chmod(Path(address).parent, 0o711)
    if os.path.lexists(address):
        info = os.lstat(address)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0: raise RuntimeError("unsafe socket")
        os.unlink(address)
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
    with Server(address, Handler) as server:
        os.chown(address, 0, owner.pw_gid)
        os.chmod(address, 0o660)
        server.serve_forever()


if __name__ == "__main__": main()
