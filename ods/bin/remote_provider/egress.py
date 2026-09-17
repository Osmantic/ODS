"""Pure helpers for the remote-provider egress service.

The FastAPI service owns HTTP I/O; this module keeps the security-sensitive
request preparation small and easy to test without sockets.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from .policy import (
    DEFAULT_POLICY_PATH,
    INTERNAL_EGRESS_BASE_URL,
    PUBLIC_MODEL_ALIAS,
    PolicyError,
    classify_forbidden_ip_address,
    load_policy,
    plan_route,
)
from .ssh_supervisor import ssh_tunnel_base_url


ROUTING_STATE_SCHEMA = "ods.remote-routing-state.v1"
FORWARD_PATHS = {
    "/v1/chat/completions": "POST",
    "/v1/completions": "POST",
    "/v1/responses": "POST",
}
SSH_STATE_ENV_KEYS = {
    "host": "REMOTE_LLM_SSH_HOST",
    "user": "REMOTE_LLM_SSH_USER",
    "port": "REMOTE_LLM_SSH_PORT",
    "inferenceHost": "REMOTE_LLM_SSH_INFERENCE_HOST",
    "inferencePort": "REMOTE_LLM_SSH_INFERENCE_PORT",
    "controlHost": "REMOTE_LLM_SSH_CONTROL_HOST",
    "controlPort": "REMOTE_LLM_SSH_CONTROL_PORT",
}
DEFAULT_MAX_BODY_BYTES = 4 * 1024 * 1024
DEFAULT_SECRET_PATH = Path("/state/remote-provider/secrets/provider-api-key")
AddressResolver = Callable[..., list[tuple[Any, ...]]]
HOP_BY_HOP_HEADERS = {
    "authorization",
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class EgressError(Exception):
    """HTTP-friendly egress preparation failure."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class UpstreamRequest:
    method: str
    url: str
    headers: dict[str, str]
    content: bytes
    requested_model: str
    provider_model: str
    stream: bool
    tls_server_name: str = ""
    host_header: str = ""
    connection_key: str = ""


def _disabled_route(mode: str = "cloud") -> dict[str, Any]:
    return {
        "schema": "ods.remote-provider-route.v1",
        "enabled": False,
        "mode": mode,
        "transport": None,
        "provider": None,
        "ssh": None,
        "egress": {
            "internalBaseUrl": INTERNAL_EGRESS_BASE_URL,
            "publicModel": PUBLIC_MODEL_ALIAS,
            "consumerRoute": "gateway",
        },
    }


def load_route_state(path: str | Path) -> dict[str, Any]:
    """Read generated remote routing state; missing means disabled."""
    state_path = Path(path)
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "schema": ROUTING_STATE_SCHEMA,
            "enabled": False,
            "mode": "cloud",
            "provider": None,
        }
    except (OSError, ValueError) as exc:
        raise EgressError(503, "invalid_route_state", str(exc)) from exc


def _route_env_from_state(state: Mapping[str, Any], provider: Mapping[str, Any]) -> dict[str, str]:
    transport = str(provider.get("transport") or "")
    env = {
        "ODS_MODE": str(state.get("mode") or "cloud"),
        "REMOTE_LLM_ENABLED": "true",
        "REMOTE_LLM_TRANSPORT": transport,
        "REMOTE_LLM_BASE_URL": str(provider.get("baseUrl") or ""),
        "REMOTE_LLM_MODEL": str(provider.get("model") or ""),
    }
    if transport != "ssh":
        return env
    ssh = state.get("ssh")
    if not isinstance(ssh, Mapping):
        raise EgressError(503, "invalid_route_state", "ssh metadata is missing")
    for state_key, env_key in SSH_STATE_ENV_KEYS.items():
        value = ssh.get(state_key)
        if value not in (None, ""):
            env[env_key] = str(value)
    return env


def route_from_state(
    state: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate generated routing-state metadata and return a public route."""
    if state.get("schema") != ROUTING_STATE_SCHEMA:
        raise EgressError(503, "invalid_route_state", "unknown routing-state schema")
    mode = str(state.get("mode") or "cloud")
    if state.get("enabled") is not True:
        return _disabled_route(mode)
    provider = state.get("provider")
    if not isinstance(provider, Mapping):
        raise EgressError(503, "invalid_route_state", "provider metadata is missing")
    try:
        return plan_route(
            _route_env_from_state({**state, "mode": mode}, provider),
            policy=policy or load_policy(DEFAULT_POLICY_PATH),
        )
    except PolicyError as exc:
        raise EgressError(503, "route_policy_rejected", str(exc)) from exc


def read_provider_secret(path: str | Path) -> str:
    """Read a provider bearer token from a private file, if present."""
    secret_path = Path(path)
    try:
        value = secret_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise EgressError(503, "provider_secret_unreadable", str(exc)) from exc
    secret = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in secret):
        raise EgressError(
            503,
            "provider_secret_invalid",
            "provider secret contains control characters",
        )
    return secret


def provider_secret_status(path: str | Path) -> dict[str, Any]:
    """Return support-bundle-safe secret-file status."""
    secret_path = Path(path)
    try:
        stat = secret_path.stat()
    except FileNotFoundError:
        return {"configured": False, "path": str(secret_path), "bytes": 0}
    except OSError:
        return {"configured": False, "path": str(secret_path), "bytes": None}
    return {"configured": stat.st_size > 0, "path": str(secret_path), "bytes": stat.st_size}


def sanitize_forward_headers(
    headers: Mapping[str, str],
    *,
    provider_secret: str,
) -> dict[str, str]:
    """Strip client auth and hop-by-hop headers; add provider auth privately."""
    forwarded: dict[str, str] = {}
    for name, value in headers.items():
        lower = name.lower()
        if lower in HOP_BY_HOP_HEADERS:
            continue
        forwarded[name] = value
    forwarded["content-type"] = "application/json"
    if provider_secret:
        forwarded["authorization"] = f"Bearer {provider_secret}"
    return forwarded


def _join_openai_path(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    suffix = path
    if suffix.startswith("/v1/"):
        suffix = suffix[len("/v1"):]
    elif suffix == "/v1":
        suffix = ""
    if suffix and not suffix.startswith("/"):
        suffix = f"/{suffix}"
    return f"{base}{suffix}"


def _provider_host_port(base_url: str) -> tuple[str, int]:
    try:
        parts = urlsplit(base_url)
        port = parts.port
    except ValueError as exc:
        raise EgressError(503, "invalid_route", f"provider URL is invalid: {exc}") from exc
    host = parts.hostname or ""
    if not host:
        raise EgressError(503, "invalid_route", "provider route is missing a host")
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return host, port


def resolve_direct_provider_addresses(
    base_url: str,
    *,
    resolver: AddressResolver = socket.getaddrinfo,
) -> list[str]:
    """Resolve a direct provider URL to unique IP addresses for policy checks."""
    host, port = _provider_host_port(base_url)
    try:
        results = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise EgressError(
            503,
            "provider_resolution_failed",
            f"remote provider host could not be resolved: {exc}",
        ) from exc
    addresses: list[str] = []
    seen: set[str] = set()
    for result in results:
        if len(result) < 5 or not isinstance(result[4], tuple) or not result[4]:
            continue
        address = str(result[4][0])
        if address and address not in seen:
            seen.add(address)
            addresses.append(address)
    if not addresses:
        raise EgressError(
            503,
            "provider_resolution_empty",
            "remote provider host did not resolve to any usable addresses",
        )
    return addresses


def validate_direct_provider_resolution(
    route: Mapping[str, Any],
    *,
    resolver: AddressResolver = socket.getaddrinfo,
) -> list[str]:
    """Fail closed when a direct provider resolves to unsafe address space."""
    if route.get("enabled") is not True or route.get("transport") != "direct":
        return []
    provider = route.get("provider")
    if not isinstance(provider, Mapping):
        raise EgressError(503, "invalid_route", "provider route is missing")
    base_url = str(provider.get("baseUrl") or "")
    host, _port = _provider_host_port(base_url)
    literal_reason = classify_forbidden_ip_address(host)
    if literal_reason:
        raise EgressError(
            503,
            "provider_resolution_rejected",
            f"remote provider URL targets {literal_reason} address space",
        )
    addresses = resolve_direct_provider_addresses(base_url, resolver=resolver)
    for address in addresses:
        reason = classify_forbidden_ip_address(address)
        if reason:
            raise EgressError(
                503,
                "provider_resolution_rejected",
                f"remote provider host resolves to {reason} address space",
            )
    return addresses


def upstream_base_url_for_route(route: Mapping[str, Any]) -> str:
    """Return the service-internal upstream URL used at the egress boundary."""
    provider = route.get("provider")
    if not isinstance(provider, Mapping):
        raise EgressError(503, "invalid_route", "provider route is missing")
    transport = route.get("transport")
    if transport == "direct":
        return str(provider.get("baseUrl") or "")
    if transport == "ssh":
        try:
            return ssh_tunnel_base_url(route)
        except (TypeError, ValueError) as exc:
            raise EgressError(503, "invalid_route", str(exc)) from exc
    raise EgressError(
        503,
        "transport_unavailable",
        "remote provider transport is not available in this egress service",
    )


def prepare_upstream_request(
    *,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes,
    route: Mapping[str, Any],
    provider_secret: str,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    resolved_addresses: list[str] | None = None,
) -> UpstreamRequest:
    """Validate and rewrite one OpenAI-compatible request for the provider."""
    required_method = FORWARD_PATHS.get(path)
    if required_method is None or method.upper() != required_method:
        raise EgressError(
            404,
            "not_forwarded",
            f"Path not served by remote-provider-egress: {path}",
        )
    if len(body) > max_body_bytes:
        raise EgressError(413, "payload_too_large", "request body exceeds limit")
    if route.get("enabled") is not True:
        raise EgressError(503, "remote_route_disabled", "remote provider route is disabled")
    if not provider_secret:
        raise EgressError(
            503,
            "missing_provider_secret",
            "provider secret file is missing or empty",
        )
    provider = route.get("provider")
    if not isinstance(provider, Mapping):
        raise EgressError(503, "invalid_route", "provider route is missing")
    upstream_base_url = upstream_base_url_for_route(route)
    tls_server_name = ""
    host_header = ""
    connection_key = ""
    if route.get("transport") == "direct" and resolved_addresses:
        parts = urlsplit(upstream_base_url)
        tls_server_name = parts.hostname or ""
        ip_address = resolved_addresses[0]
        if ":" in ip_address and not ip_address.startswith("["):
            ip_address = f"[{ip_address}]"
        netloc = ip_address
        if parts.port is not None:
            netloc = f"{ip_address}:{parts.port}"
        original_host = tls_server_name
        if ":" in original_host and not original_host.startswith("["):
            original_host = f"[{original_host}]"
        host_header = original_host
        if parts.port is not None:
            host_header = f"{original_host}:{parts.port}"
        tls_port = parts.port or (443 if parts.scheme == "https" else 80)
        connection_key = f"{parts.scheme}://{tls_server_name}:{tls_port}"
        upstream_base_url = urlunsplit((
            parts.scheme,
            netloc,
            parts.path,
            parts.query,
            parts.fragment
        ))
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EgressError(400, "invalid_json", f"invalid JSON body: {exc}") from exc
    if not isinstance(payload, dict):
        raise EgressError(400, "invalid_json", "request body must be a JSON object")
    requested_model = str(payload.get("model") or PUBLIC_MODEL_ALIAS)
    provider_model = str(provider.get("model") or "")
    payload["model"] = provider_model
    content = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return UpstreamRequest(
        method=required_method,
        url=_join_openai_path(upstream_base_url, path),
        headers=sanitize_forward_headers(headers, provider_secret=provider_secret),
        content=content,
        requested_model=requested_model,
        provider_model=provider_model,
        stream=bool(payload.get("stream")),
        tls_server_name=tls_server_name,
        host_header=host_header,
        connection_key=connection_key,
    )


__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "DEFAULT_SECRET_PATH",
    "FORWARD_PATHS",
    "ROUTING_STATE_SCHEMA",
    "EgressError",
    "UpstreamRequest",
    "load_route_state",
    "prepare_upstream_request",
    "provider_secret_status",
    "read_provider_secret",
    "route_from_state",
    "sanitize_forward_headers",
    "resolve_direct_provider_addresses",
    "upstream_base_url_for_route",
    "validate_direct_provider_resolution",
]
