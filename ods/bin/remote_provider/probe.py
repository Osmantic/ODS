"""Remote-provider lifecycle probe helpers.

The host-agent owns lifecycle initiation; this module keeps the direct-provider
handshake small, stdlib-only, and testable without opening sockets.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable, Mapping
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from .egress import (
    EgressError,
    upstream_base_url_for_route,
    validate_direct_provider_resolution,
)


DEFAULT_PROBE_TIMEOUT_SECONDS = 10.0
MAX_PROBE_RESPONSE_BYTES = 64 * 1024
PROBE_RECEIPT_SCHEMA = "ods.remote-provider-probe-receipt.v1"
UrlOpener = Callable[..., Any]


class ProbeError(Exception):
    """HTTP-friendly remote-provider probe failure."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)


def _models_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


def _provider(route: Mapping[str, Any]) -> Mapping[str, Any]:
    provider = route.get("provider")
    if not isinstance(provider, Mapping):
        raise ProbeError(503, "invalid_route", "provider route is missing")
    return provider


def _transport(route: Mapping[str, Any]) -> str:
    return str(route.get("transport") or "")


def _read_probe_body(response: Any) -> bytes:
    body = response.read(MAX_PROBE_RESPONSE_BYTES + 1)
    if len(body) > MAX_PROBE_RESPONSE_BYTES:
        raise ProbeError(
            502,
            "provider_probe_too_large",
            "remote provider probe response exceeded the safety limit",
        )
    return body


def _model_count(body: bytes) -> int:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ProbeError(
            502,
            "provider_invalid_response",
            "remote provider models endpoint returned invalid JSON",
        )
    if not isinstance(payload, Mapping):
        raise ProbeError(
            502,
            "provider_invalid_response",
            "remote provider models endpoint did not return an object",
        )
    models = payload.get("data")
    if not isinstance(models, list):
        raise ProbeError(
            502,
            "provider_invalid_response",
            "remote provider models endpoint did not return a data array",
        )
    return len(models)


def _safe_int(value: Any) -> int | None:
    return value if type(value) is int else None


def _safe_text(value: Any, *, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        return ""
    return text[:max_length]


def public_probe_receipt(
    probe_result: Mapping[str, Any],
    *,
    verified_at: str,
) -> dict[str, Any]:
    """Return a support-bundle-safe provider probe receipt."""
    result = probe_result if isinstance(probe_result, Mapping) else {}
    resolution = result.get("resolution")
    clean_resolution = None
    if isinstance(resolution, Mapping):
        clean_resolution = {
            "ok": bool(resolution.get("ok")),
            "addressCount": _safe_int(resolution.get("addressCount")),
        }

    receipt: dict[str, Any] = {
        "schema": PROBE_RECEIPT_SCHEMA,
        "ok": bool(result.get("ok")),
        "verifiedAt": _safe_text(verified_at, max_length=64),
        "endpoint": _safe_text(result.get("endpoint"), max_length=32),
        "httpStatus": _safe_int(result.get("status")),
        "modelCount": _safe_int(result.get("modelCount")),
        "resolution": clean_resolution,
    }
    content_type = _safe_text(result.get("contentType"), max_length=128)
    if content_type:
        receipt["contentType"] = content_type
    return receipt


def _probe_models_endpoint(
    *,
    base_url: str,
    provider_secret: str,
    resolution: Mapping[str, Any],
    transport: str,
    opener: UrlOpener,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    secret = str(provider_secret or "").strip()
    if not secret:
        raise ProbeError(400, "missing_provider_secret", "provider secret is required")

    request = urllib_request.Request(
        _models_url(base_url),
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {secret}",
            "User-Agent": "ODS remote-provider-probe",
        },
    )
    try:
        with opener(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            body = _read_probe_body(response)
            content_type = str(response.headers.get("content-type", ""))
    except urllib_error.HTTPError as exc:
        raise ProbeError(
            int(exc.code),
            "provider_http_error",
            f"remote provider probe returned HTTP {int(exc.code)}",
        ) from exc
    except (TimeoutError, urllib_error.URLError, OSError) as exc:
        raise ProbeError(
            502,
            "provider_unreachable",
            f"remote provider probe failed: {exc}",
        ) from exc

    if status < 200 or status >= 300:
        raise ProbeError(
            status,
            "provider_http_error",
            f"remote provider probe returned HTTP {status}",
        )
    return {
        "ok": True,
        "status": status,
        "endpoint": "/v1/models",
        "transport": transport,
        "contentType": content_type,
        "modelCount": _model_count(body),
        "resolution": dict(resolution),
    }


def probe_provider_route(
    route: Mapping[str, Any],
    *,
    provider_secret: str,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
    opener: UrlOpener = urllib_request.urlopen,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Probe an OpenAI-compatible provider through the route's transport boundary."""
    if route.get("enabled") is not True:
        raise ProbeError(503, "remote_route_disabled", "remote provider route is disabled")
    transport = _transport(route)
    if transport == "direct":
        try:
            resolved_addresses = validate_direct_provider_resolution(route, resolver=resolver)
        except EgressError as exc:
            raise ProbeError(exc.status, exc.code, exc.message) from exc
        base_url = str(_provider(route).get("baseUrl") or "")
        resolution = {"ok": True, "addressCount": len(resolved_addresses)}
    elif transport == "ssh":
        try:
            base_url = upstream_base_url_for_route(route)
        except EgressError as exc:
            raise ProbeError(exc.status, exc.code, exc.message) from exc
        resolution = {"ok": True, "addressCount": 0}
    else:
        raise ProbeError(
            501,
            "transport_probe_unavailable",
            "remote provider test probes require direct or ssh transport",
        )
    return _probe_models_endpoint(
        base_url=base_url,
        provider_secret=provider_secret,
        resolution=resolution,
        transport=transport,
        opener=opener,
        timeout=timeout,
    )


def probe_direct_provider(
    route: Mapping[str, Any],
    *,
    provider_secret: str,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
    opener: UrlOpener = urllib_request.urlopen,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Probe an OpenAI-compatible direct provider without leaking credentials."""
    if route.get("enabled") is not True:
        raise ProbeError(503, "remote_route_disabled", "remote provider route is disabled")
    if _transport(route) != "direct":
        raise ProbeError(
            501,
            "transport_probe_unavailable",
            "remote provider direct probes require direct transport",
        )
    return probe_provider_route(
        route,
        provider_secret=provider_secret,
        resolver=resolver,
        opener=opener,
        timeout=timeout,
    )


__all__ = [
    "DEFAULT_PROBE_TIMEOUT_SECONDS",
    "MAX_PROBE_RESPONSE_BYTES",
    "PROBE_RECEIPT_SCHEMA",
    "ProbeError",
    "probe_direct_provider",
    "probe_provider_route",
    "public_probe_receipt",
]
