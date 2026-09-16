"""Pure egress probe response helpers for remote-provider services."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .egress import EgressError
from .probe import (
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    ProbeError,
    probe_provider_route,
    public_probe_receipt,
)

PROBE_RESPONSE_SCHEMA = "ods.remote-provider-egress-probe.v1"
RouteProbe = Callable[..., Mapping[str, Any]]


def probe_route_response(
    route: Mapping[str, Any],
    *,
    provider_secret: str,
    verified_at: str,
    tunnel: Mapping[str, Any] | None = None,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    probe: RouteProbe = probe_provider_route,
) -> dict[str, Any]:
    """Return a support-bundle-safe egress probe response for a validated route."""
    if route.get("transport") == "ssh":
        if not isinstance(tunnel, Mapping) or tunnel.get("ready") is not True:
            raise EgressError(503, "ssh_tunnel_not_ready", "SSH tunnel is not ready")
    probe_result = probe(
        route,
        provider_secret=provider_secret,
        timeout=timeout,
    )
    return {
        "schema": PROBE_RESPONSE_SCHEMA,
        "ok": True,
        "transport": route.get("transport"),
        "probe": public_probe_receipt(probe_result, verified_at=verified_at),
        "tunnel": dict(tunnel) if isinstance(tunnel, Mapping) else None,
    }


__all__ = [
    "PROBE_RESPONSE_SCHEMA",
    "ProbeError",
    "probe_route_response",
]
