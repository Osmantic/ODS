"""Runtime selection for dashboard-api: Docker (default) or Windows-native.

``ODS_RUNTIME=native`` runs dashboard-api as a plain process next to a native
llama-server, with no Docker, WSL, nginx or host agent. In that mode:

- Only the services named in ``ODS_NATIVE_SERVICES`` exist (default
  ``llama-server,dashboard-api``). Status and readiness report exactly those,
  so an absent service is never probed and never shows as down.
- Every service is reached on 127.0.0.1 at its published port.
- llama-server requires its API key (``--api-key-file``); dashboard-api reads
  the same key from ``LLAMA_SERVER_API_KEY_FILE`` (or ``LLAMA_SERVER_API_KEY``)
  and sends it only to that llama-server origin.
- ``native_gateway`` serves the dashboard SPA and takes nginx's place.
- Docker-only features are unavailable (``UNAVAILABLE_FEATURES``); their API
  routes answer 404 ``native_unavailable`` and the UI does not offer them.

Unset means ``native`` on Windows and ``docker`` elsewhere, so the container
behaviour is unchanged. Any other value is a configuration error and stops the
process at import.

``native_chat`` serves llama-server's own web UI on a second loopback port
(``ODS_NATIVE_CHAT_PORT``) behind the same gate, adding the llama-server key.
"""

from __future__ import annotations

import functools
import os
import re
import sys
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

RUNTIME_DOCKER = "docker"
RUNTIME_NATIVE = "native"
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_NATIVE_SERVICES = ("llama-server", "dashboard-api")
_SERVICE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def parse_runtime(value: str | None, platform: str = sys.platform) -> str:
    # dashboard-api on Windows itself is always the native runtime: Docker and
    # WSL installs run it in a Linux container.
    default = RUNTIME_NATIVE if platform == "win32" else RUNTIME_DOCKER
    runtime = (value or "").strip().lower() or default
    if runtime not in (RUNTIME_DOCKER, RUNTIME_NATIVE):
        raise ValueError(f"ODS_RUNTIME must be 'docker' or 'native', got {value!r}")
    return runtime


def parse_enabled_services(value: str | None) -> frozenset[str]:
    """The services a native install runs, from a comma-separated list."""
    if value is None or not value.strip():
        return frozenset(DEFAULT_NATIVE_SERVICES)
    services = [item.strip() for item in value.split(",") if item.strip()]
    invalid = [item for item in services if not _SERVICE_ID_RE.fullmatch(item)]
    if invalid or not services:
        raise ValueError(f"ODS_NATIVE_SERVICES has invalid service ids: {invalid or value!r}")
    return frozenset(services)


RUNTIME = parse_runtime(os.environ.get("ODS_RUNTIME"))
NATIVE = RUNTIME == RUNTIME_NATIVE
# None in Docker mode: the Compose stack decides which services exist there.
ENABLED_SERVICES: frozenset[str] | None = (
    parse_enabled_services(os.environ.get("ODS_NATIVE_SERVICES")) if NATIVE else None
)


# Ports a native install sets per service, where Docker's published-port
# variable does not describe the native process (OLLAMA_PORT is Docker's 11434).
NATIVE_PORT_ENV = {"llama-server": ("LLAMA_SERVER_PORT", 8080)}


def native_port_override(service_id: str, env: Mapping[str, str]) -> int | None:
    """The native listening port for *service_id*, or None to use the manifest's."""
    if service_id not in NATIVE_PORT_ENV:
        return None
    name, default = NATIVE_PORT_ENV[service_id]
    raw = (env.get(name) or "").strip()
    port = int(raw) if raw else default
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be a port from 1 to 65535, got {raw!r}")
    return port


def parse_chat_port(value: str | None) -> int | None:
    """``ODS_NATIVE_CHAT_PORT``: where the supervisor runs native_chat, if it does."""
    if value is None or not value.strip():
        return None
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError(f"ODS_NATIVE_CHAT_PORT must be a port from 1 to 65535, got {value!r}")
    return port


NATIVE_CHAT_PORT = parse_chat_port(os.environ.get("ODS_NATIVE_CHAT_PORT")) if NATIVE else None


# --- llama-server API key ----------------------------------------------------

def read_llama_server_api_key(env: Mapping[str, str]) -> str:
    """The native llama-server key: the key file's content, else the env value.

    The key file is the one llama-server itself reads (``--api-key-file``).
    A configured file that cannot be read is an error, not "no key".
    """
    key_file = (env.get("LLAMA_SERVER_API_KEY_FILE") or "").strip()
    if key_file:
        key = Path(key_file).read_text(encoding="utf-8").strip()
        if not key:
            raise ValueError(f"LLAMA_SERVER_API_KEY_FILE {key_file} is empty")
    else:
        key = (env.get("LLAMA_SERVER_API_KEY") or "").strip()
    if key and any(ord(char) <= 32 or ord(char) >= 127 for char in key):
        raise ValueError("the llama-server API key must be printable ASCII without spaces")
    return key


@functools.lru_cache(maxsize=1)
def llama_server_api_key() -> str:
    return read_llama_server_api_key(os.environ) if NATIVE else ""


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return parts.scheme.lower(), parts.hostname.lower(), port


def bearer_for_origin(url: str, llama_host: str, llama_port: int, key: str) -> dict[str, str]:
    """``Authorization`` for *url* only when it is the llama-server origin.

    The key is destination-bound: a configured external or gateway URL never
    receives it, whatever path or redirect led there.
    """
    if not key or _origin(url) != ("http", str(llama_host).lower(), int(llama_port)):
        return {}
    return {"Authorization": f"Bearer {key}"}


# --- Docker-only features ----------------------------------------------------

# feature id -> (methods or None for all, path prefixes). The UI reads the ids
# from /api/status ``runtime.unavailable``; native_gateway answers the routes.
UNAVAILABLE_FEATURES: dict[str, tuple[frozenset[str] | None, tuple[str, ...]]] = {
    # Library installs, enables and updates drive Docker Compose.
    "extensions": (frozenset({"POST", "PUT", "PATCH", "DELETE"}), ("/api/extensions/",)),
    # Talk needs Hermes; Portal (Pixel) needs pixel-agent, pixel-edge and LiteLLM.
    "talk": (None, ("/api/talk/",)),
    "pixel": (None, ("/api/pixel/chat", "/api/pixel/teams", "/api/pixel/images", "/pixel-preview/")),
    "resources": (None, ("/api/services/resources",)),
    "preflight-docker": (None, ("/api/preflight/docker",)),
    "tailscale": (None, ("/api/tailscale/",)),
    "wifi": (None, ("/api/setup/wifi-", "/api/setup/network-status")),
}


def unavailable_feature(method: str, path: str) -> str | None:
    """The Docker-only feature a native-mode request would reach, if any."""
    for feature, (methods, prefixes) in UNAVAILABLE_FEATURES.items():
        if (methods is None or method.upper() in methods) and path.startswith(prefixes):
            return feature
    return None


def runtime_summary() -> dict:
    """What /api/status reports so the UI can hide Docker-only features."""
    return {
        "kind": RUNTIME,
        "unavailable": sorted(UNAVAILABLE_FEATURES) if NATIVE else [],
    }
