"""Probe a host-native llama-server through this installation's router container.

A WSL host agent cannot reach a Windows loopback-only listener: WSL localhost
is not Windows localhost. Docker Desktop containers can, through
``host.docker.internal``. This module runs one bounded stdlib request inside
the owned, running model-router container, against exactly the
``llama-server-default`` origin that the router itself serves inference from.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit


ENDPOINT_ID = "llama-server-default"
_LIMIT = 65536
_PATHS = frozenset({"/health", "/props", "/v1/models", "/v1/chat/completions", "/metrics"})
_INSPECT = ('{"Id":{{json .Id}},"Running":{{json .State.Running}},'
            '"Project":{{json (index .Config.Labels "com.docker.compose.project")}},'
            '"Service":{{json (index .Config.Labels "com.docker.compose.service")}},'
            '"Mounts":{{json .Mounts}}}')

# Credentials and request data arrive only on stdin. A worker deadline also
# ends a slow-drip response after the local Docker client has disconnected.
_WORKER = r'''
import json
import os
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.request

LIMIT = 65536

class ProofError(ValueError):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect rejected", headers, fp)

def deadline():
    sys.stderr.write("runtime request timed out\n")
    sys.stderr.flush()
    os._exit(124)

def serve():
    raw = sys.stdin.buffer.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ProofError("request exceeds 64 KiB")
    message = json.loads(raw)
    timer = threading.Timer(message["timeout"], deadline)
    timer.daemon = True
    timer.start()
    try:
        with Path("/config/endpoints.json").open("rb") as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ProofError("endpoint configuration exceeds 64 KiB")
        endpoints = json.loads(raw)["endpoints"]
        matches = [row for row in endpoints if isinstance(row, dict)
                   and row.get("id") == message["endpoint_id"]]
        if len(matches) != 1 or matches[0].get("baseUrl", "") != message["origin"]:
            raise ProofError("configured runtime endpoint does not match")
        headers = {"Content-Type": "application/json"}
        if message["api_key"]:
            headers["Authorization"] = "Bearer " + message["api_key"]
        payload = message["payload"]
        body = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(message["origin"] + message["path"],
                                         data=body, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=message["timeout"]) as response:
            body = response.read(LIMIT + 1)
        if len(body) > LIMIT:
            raise ProofError("runtime response exceeds 64 KiB")
        body.decode("utf-8")
        sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()
    finally:
        timer.cancel()

try:
    serve()
except urllib.error.HTTPError as exc:
    sys.stderr.write("runtime returned HTTP %d\n" % exc.code)
    sys.exit(1)
except ProofError as exc:
    sys.stderr.write("runtime transport failed: %s\n" % exc)
    sys.exit(1)
except (OSError, ValueError, KeyError, TypeError) as exc:
    sys.stderr.write("runtime transport failed: %s\n" % type(exc).__name__)
    sys.exit(1)
'''


def _docker(arguments: list[str], *, timeout: float, data: bytes | None = None) -> bytes:
    result = subprocess.run(["docker", *arguments], input=data, capture_output=True,
                            timeout=timeout, check=False)
    if len(result.stdout) > _LIMIT or len(result.stderr) > _LIMIT:
        raise OSError("Docker runtime transport output exceeds 64 KiB")
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:512]
        raise OSError(f"Docker runtime transport failed (exit {result.returncode}): {detail}")
    return result.stdout


def _owned_router(install_dir: Path, project: str) -> str:
    candidates = _docker(["ps", "--no-trunc", "--quiet", "--filter", "status=running",
                          "--filter", f"label=com.docker.compose.project={project}",
                          "--filter", "label=com.docker.compose.service=model-router"],
                         timeout=10).decode("ascii").splitlines()
    if len(candidates) != 1 or not re.fullmatch(r"[0-9a-f]{64}", candidates[0]):
        raise OSError("Expected exactly one running ODS model-router container")
    container_id = candidates[0]
    info = json.loads(_docker(["inspect", "--type", "container", "--format", _INSPECT,
                              container_id], timeout=10))
    if (not isinstance(info, dict) or info.get("Id") != container_id or info.get("Running") is not True
            or info.get("Project") != project or info.get("Service") != "model-router"):
        raise OSError("ODS model-router ownership or running state changed")
    all_mounts = info.get("Mounts")
    if not isinstance(all_mounts, list) or any(not isinstance(mount, dict) for mount in all_mounts):
        raise OSError("ODS model-router mount ownership metadata is invalid")
    root = Path(install_dir).resolve()
    for target, relative in (("/state", "data"),
                             ("/config", "config/model-router")):
        mounts = [mount for mount in all_mounts if mount.get("Destination") == target]
        if (len(mounts) != 1 or mounts[0].get("Type") != "bind"
                or mounts[0].get("RW") is not False
                or not isinstance(mounts[0].get("Source"), str)
                or Path(mounts[0].get("Source", "")) != root / relative):
            raise OSError(f"ODS model-router mount does not belong to this installation: {target}")
    return container_id


def request(install_dir: Path, origin: str, path: str,
            payload: dict | None = None, api_key: str = "", timeout: float = 5,
            *, project: str = "ods") -> str:
    """Return bounded HTTP text; never grant readiness or publish model state.

    ``origin`` is the router's ``llama-server-default`` base URL without a
    path; ``path`` is one of the proof and telemetry routes.
    """
    parsed = urlsplit(origin)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path != ""
            or any(ord(char) <= 32 or ord(char) == 127 for char in origin)):
        raise ValueError("Runtime origin must be a credential-free HTTP(S) origin without a path")
    _ = parsed.port
    if path not in _PATHS:
        raise ValueError("Unsupported runtime observation route")
    if (path == "/v1/chat/completions") != isinstance(payload, dict):
        raise ValueError("Only a chat-completion proof accepts a JSON payload")
    if not isinstance(api_key, str) or "\r" in api_key or "\n" in api_key:
        raise ValueError("Invalid runtime API key")
    if not math.isfinite(timeout) or not 0 < timeout <= 900:
        raise ValueError("Runtime proof timeout must be between 0 and 900 seconds")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise ValueError("Invalid Compose project")
    data = json.dumps(dict(endpoint_id=ENDPOINT_ID, origin=origin, path=path, payload=payload,
                           api_key=api_key, timeout=timeout), allow_nan=False).encode("utf-8")
    if len(data) > _LIMIT:
        raise ValueError("Runtime proof request exceeds 64 KiB")
    container_id = _owned_router(install_dir, project)
    body = _docker(["exec", "-i", container_id, "python", "-I", "-S", "-c", _WORKER],
                   data=data, timeout=timeout + 10)
    return body.decode("utf-8")
