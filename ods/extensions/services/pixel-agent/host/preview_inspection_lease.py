"""Fixed no-network capsule control protocol for one bounded document lease."""

import os
import socket
import sys
import threading
import time

from preview_inspection_protocol import (
    Invalid, MAX_BUNDLE, MAX_RESULT, canonical, exact, strict_json, validate_bundle,
)

SOCKET = "/tmp/ods-inspection-lease.sock"
LIFETIME = 120
IDLE = 45
MAX_CALLS = 8


def receive(connection, limit):
    data = bytearray()
    while len(data) <= limit:
        chunk = connection.recv(min(65536, limit + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in data:
            break
    if len(data) > limit or not data.endswith(b"\n") or data.count(b"\n") != 1:
        raise Invalid("invalid lease framing")
    return strict_json(data)


def respond(connection, value):
    encoded = canonical(value)
    if len(encoded) > MAX_RESULT:
        raise Invalid("lease result too large")
    connection.sendall(encoded + b"\n")


def validate_binding(value):
    import re
    exact(value, ("scope", "leaseId"))
    if not isinstance(value["scope"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["scope"]):
        raise Invalid("invalid lease scope")
    if not isinstance(value["leaseId"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["leaseId"]):
        raise Invalid("invalid lease capability")
    return value


def serve():
    from playwright.sync_api import sync_playwright
    from preview_inspection_document import InspectionBrowser
    deadline = time.monotonic() + LIFETIME
    # PID 1 exits even if Chromium or a page ceases responding. The outer host
    # owns exact-container removal too; neither pipe EOF nor a receipt proves it.
    watchdog = threading.Timer(LIFETIME, lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(SOCKET)
        os.chmod(SOCKET, 0o600)
        listener.listen(1)
        listener.settimeout(IDLE)
        try:
            initial, _ = listener.accept()
            with initial:
                initial.settimeout(5)
                message = receive(initial, MAX_BUNDLE)
                exact(message, ("operation", "binding", "bundle"))
                if message["operation"] != "open":
                    raise Invalid("lease must open first")
                binding = validate_binding(message["binding"])
                request, _ = validate_bundle(message["bundle"])

                def driver(references, execute):
                    snapshot = references.snapshot()
                    respond(initial, {"schemaVersion": 2, "kind": "ods-pixel-preview-snapshot",
                        "status": "snapshot", **binding, "siteId": request["siteId"],
                        "sha256": request["sha256"], "viewport": request["viewport"], **snapshot,
                        "maximumLifetimeSeconds": LIFETIME, "idleSeconds": IDLE})
                    initial.shutdown(socket.SHUT_WR)
                    for _ in range(MAX_CALLS - 1):
                        remaining = min(IDLE, deadline - time.monotonic())
                        if remaining <= 0:
                            raise Invalid("lease expired")
                        listener.settimeout(remaining)
                        connection, _ = listener.accept()
                        with connection:
                            connection.settimeout(5)
                            command = receive(connection, 8192)
                            if not isinstance(command, dict) or command.get("binding") != binding:
                                raise Invalid("lease binding changed")
                            if command.get("operation") == "close":
                                exact(command, ("operation", "binding"))
                                respond(connection, {"schemaVersion": 2, "kind": "ods-pixel-preview-lease",
                                    "status": "closed", **binding})
                                return
                            exact(command, ("operation", "binding", "request"))
                            if command["operation"] != "inspect":
                                raise Invalid("unsupported lease operation")
                            result = execute(command["request"])
                            respond(connection, {"schemaVersion": 2, "kind": "ods-pixel-preview-lease",
                                "status": "inspected", **binding,
                                "documentGeneration": references.generation, "result": result})
                with InspectionBrowser(sync_playwright, lifetime=LIFETIME, calls=1) as browser:
                    browser.inspect(message["bundle"], document_driver=driver)
        finally:
            watchdog.cancel()
            try:
                os.unlink(SOCKET)
            except FileNotFoundError:
                pass


def client():
    raw = sys.stdin.buffer.read(MAX_BUNDLE + 1)
    if len(raw) > MAX_BUNDLE:
        raise Invalid("lease request too large")
    message = strict_json(raw)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(45)
        until = time.monotonic() + 2
        while True:
            try:
                connection.connect(SOCKET)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() >= until:
                    raise Invalid("lease unavailable")
                time.sleep(0.05)
        connection.sendall(canonical(message) + b"\n")
        result = receive(connection, MAX_RESULT)
    sys.stdout.buffer.write(canonical(result) + b"\n")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["serve"]:
            serve()
        elif sys.argv[1:] == ["client"]:
            client()
        else:
            raise Invalid("unsupported capsule command")
    except Exception:
        # Protocol failure must terminate the lease rather than reconstruct DOM.
        sys.exit(1)
