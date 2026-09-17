"""ODS remote-provider egress service.

The service is internal-only. It accepts the OpenAI-compatible paths LiteLLM
uses, validates generated route state against the shared policy contract, and
injects provider credentials from a private file at the final egress boundary.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from remote_provider.egress import (
    DEFAULT_MAX_BODY_BYTES,
    DEFAULT_SECRET_PATH,
    EgressError,
    load_route_state,
    prepare_upstream_request,
    provider_secret_status,
    read_provider_secret,
    route_from_state,
    validate_direct_provider_resolution,
)
from remote_provider.egress_probe import probe_route_response
from remote_provider.policy import DEFAULT_POLICY_PATH, load_policy
from remote_provider.probe import (
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    ProbeError,
)


ROUTE_PATH = Path(
    os.environ.get(
        "ODS_REMOTE_PROVIDER_ROUTE_PATH",
        "/state/remote-provider/routing-state.json",
    )
)
POLICY_PATH = Path(
    os.environ.get("ODS_REMOTE_PROVIDER_POLICY_PATH", str(DEFAULT_POLICY_PATH))
)
SECRET_PATH = Path(
    os.environ.get("ODS_REMOTE_PROVIDER_API_KEY_FILE", str(DEFAULT_SECRET_PATH))
)
MAX_BODY_BYTES = int(
    os.environ.get("ODS_REMOTE_PROVIDER_MAX_BODY_BYTES", str(DEFAULT_MAX_BODY_BYTES))
)
UPSTREAM_TIMEOUT_SECONDS = float(
    os.environ.get("ODS_REMOTE_PROVIDER_UPSTREAM_TIMEOUT", "600")
)
SSH_TUNNEL_HEALTH_URL = os.environ.get(
    "ODS_REMOTE_PROVIDER_SSH_TUNNEL_HEALTH_URL",
    "http://remote-provider-ssh-tunnel:18090/health",
)
SSH_TUNNEL_HEALTH_TIMEOUT_SECONDS = float(
    os.environ.get("ODS_REMOTE_PROVIDER_SSH_TUNNEL_HEALTH_TIMEOUT", "2")
)
PROBE_TIMEOUT_SECONDS = float(
    os.environ.get(
        "ODS_REMOTE_PROVIDER_PROBE_TIMEOUT",
        str(DEFAULT_PROBE_TIMEOUT_SECONDS),
    )
)
app = FastAPI(title="ODS Remote Provider Egress", docs_url=None, redoc_url=None, openapi_url=None)

_HOP_BY_HOP_RESPONSE_HEADERS = {
    "connection",
    "content-encoding",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _safe_route_summary(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(route.get("enabled")),
        "mode": route.get("mode"),
        "transport": route.get("transport"),
        "provider": route.get("provider") if route.get("enabled") else None,
        "egress": route.get("egress"),
    }


def _load_route() -> dict[str, Any]:
    policy = load_policy(POLICY_PATH)
    return route_from_state(load_route_state(ROUTE_PATH), policy=policy)


def _http_client(connection_key: str = "") -> httpx.AsyncClient:
    if connection_key:
        clients = getattr(app.state, "direct_http_clients", None)
        if clients is None:
            clients = {}
            app.state.direct_http_clients = clients
        client = clients.get(connection_key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
            clients[connection_key] = client
        return client
    client = getattr(app.state, "http", None)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
        app.state.http = client
    return client


def _error_response(exc: EgressError) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "message": exc.message,
                "type": exc.code,
                "code": str(exc.status),
            }
        },
        status_code=exc.status,
    )


def _probe_error_response(exc: ProbeError) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "message": exc.message,
                "type": exc.code,
                "code": str(exc.status),
            }
        },
        status_code=exc.status,
    )


def _response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in _HOP_BY_HOP_RESPONSE_HEADERS
    }


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_tunnel_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    process = payload.get("process")
    if not isinstance(process, Mapping):
        process = {}
    ready = payload.get("ready") is True
    return {
        "ok": ready,
        "ready": ready,
        "status": payload.get("status"),
        "reason": payload.get("reason") or ("ready" if ready else "ssh_tunnel_not_ready"),
        "process": {
            "status": process.get("status"),
            "pid": process.get("pid"),
        },
    }


async def _ssh_tunnel_status() -> dict[str, Any]:
    client = _http_client()
    try:
        response = await client.get(
            SSH_TUNNEL_HEALTH_URL,
            timeout=SSH_TUNNEL_HEALTH_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        return {
            "ok": False,
            "ready": False,
            "status": "unavailable",
            "reason": "ssh_tunnel_timeout",
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "ready": False,
            "status": "unavailable",
            "reason": "ssh_tunnel_unavailable",
            "errorType": exc.__class__.__name__,
        }
    if response.status_code != 200:
        return {
            "ok": False,
            "ready": False,
            "status": "unavailable",
            "reason": "ssh_tunnel_unavailable",
            "httpStatus": response.status_code,
        }
    try:
        payload = response.json()
    except ValueError:
        return {
            "ok": False,
            "ready": False,
            "status": "invalid",
            "reason": "ssh_tunnel_health_invalid",
        }
    if not isinstance(payload, Mapping):
        return {
            "ok": False,
            "ready": False,
            "status": "invalid",
            "reason": "ssh_tunnel_health_invalid",
        }
    return _safe_tunnel_summary(payload)


@app.get("/health")
async def health() -> dict[str, Any]:
    secret = provider_secret_status(SECRET_PATH)
    try:
        route = _load_route()
    except EgressError as exc:
        return {
            "status": "degraded",
            "ready": False,
            "reason": exc.code,
            "route": None,
            "secret": secret,
        }
    if route.get("enabled") is not True:
        return {
            "status": "disabled",
            "ready": False,
            "reason": "remote_route_disabled",
            "route": _safe_route_summary(route),
            "secret": secret,
        }
    try:
        resolved_addresses = validate_direct_provider_resolution(route)
    except EgressError as exc:
        return {
            "status": "degraded",
            "ready": False,
            "reason": exc.code,
            "route": _safe_route_summary(route),
            "resolution": {"ok": False, "reason": exc.code},
            "secret": secret,
        }
    resolution = {"ok": True, "addressCount": len(resolved_addresses)}
    if not secret["configured"]:
        return {
            "status": "degraded",
            "ready": False,
            "reason": "missing_provider_secret",
            "route": _safe_route_summary(route),
            "resolution": resolution,
            "secret": secret,
        }
    tunnel = None
    if route.get("transport") == "ssh":
        tunnel = await _ssh_tunnel_status()
        if tunnel["ready"] is not True:
            return {
                "status": "degraded",
                "ready": False,
                "reason": "ssh_tunnel_not_ready",
                "route": _safe_route_summary(route),
                "resolution": resolution,
                "secret": secret,
                "tunnel": tunnel,
            }
    return {
        "status": "ok",
        "ready": True,
        "reason": "ready",
        "route": _safe_route_summary(route),
        "resolution": resolution,
        "secret": secret,
        "tunnel": tunnel,
    }


@app.get("/v1/models")
async def list_models() -> Response:
    try:
        route = _load_route()
    except EgressError as exc:
        return _error_response(exc)
    if route.get("enabled") is not True:
        return _error_response(
            EgressError(503, "remote_route_disabled", "remote provider route is disabled")
        )
    provider = route["provider"]
    data = [
        {"id": "ods/current", "object": "model", "owned_by": "ods"},
        {"id": "default", "object": "model", "owned_by": "ods"},
        {"id": provider["model"], "object": "model", "owned_by": "remote-provider"},
    ]
    return JSONResponse({"object": "list", "data": data, "ods": _safe_route_summary(route)})


@app.post("/probe")
async def probe() -> Response:
    tunnel = None
    try:
        route = _load_route()
        if route.get("transport") == "ssh":
            tunnel = await _ssh_tunnel_status()
        secret = read_provider_secret(SECRET_PATH)
        payload = probe_route_response(
            route,
            provider_secret=secret,
            verified_at=_iso_now(),
            tunnel=tunnel,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except EgressError as exc:
        return _error_response(exc)
    except ProbeError as exc:
        return _probe_error_response(exc)

    return JSONResponse(payload)


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "CONNECT"],
)
async def forward(full_path: str, request: Request) -> Response:
    path = "/" + full_path
    try:
        route = _load_route()
        resolved_addresses = validate_direct_provider_resolution(route)
        secret = read_provider_secret(SECRET_PATH)
        if route.get("transport") == "ssh":
            tunnel = await _ssh_tunnel_status()
            if tunnel["ready"] is not True:
                return _error_response(
                    EgressError(503, "ssh_tunnel_not_ready", "SSH tunnel is not ready")
                )
        upstream_request = prepare_upstream_request(
            method=request.method,
            path=path,
            headers=dict(request.headers),
            body=await request.body(),
            route=route,
            provider_secret=secret,
            max_body_bytes=MAX_BODY_BYTES,
            resolved_addresses=resolved_addresses,
        )
    except EgressError as exc:
        return _error_response(exc)

    client = _http_client(upstream_request.connection_key)
    headers = dict(upstream_request.headers)
    extensions = {}
    if upstream_request.tls_server_name:
        extensions["sni_hostname"] = upstream_request.tls_server_name
        headers["host"] = upstream_request.host_header

    ods_headers = {
        "X-ODS-Remote-Transport": str(route.get("transport") or ""),
        "X-ODS-Requested-Model": upstream_request.requested_model,
        "X-ODS-Provider-Model": upstream_request.provider_model,
    }
    try:
        if upstream_request.stream:
            req = client.build_request(
                upstream_request.method,
                upstream_request.url,
                content=upstream_request.content,
                headers=headers,
                timeout=UPSTREAM_TIMEOUT_SECONDS,
                extensions=extensions,
            )
            upstream = await client.send(req, stream=True)

            async def stream_body() -> AsyncIterator[bytes]:
                try:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
                finally:
                    await upstream.aclose()

            return StreamingResponse(
                stream_body(),
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type", "text/event-stream"),
                headers={**_response_headers(upstream.headers), **ods_headers},
            )

        req = client.build_request(
            upstream_request.method,
            upstream_request.url,
            content=upstream_request.content,
            headers=headers,
            timeout=UPSTREAM_TIMEOUT_SECONDS,
            extensions=extensions,
        )
        upstream = await client.send(req)
    except httpx.TimeoutException:
        return _error_response(
            EgressError(504, "upstream_timeout", "remote provider timed out")
        )
    except httpx.HTTPError as exc:
        return _error_response(
            EgressError(
                502,
                "upstream_unavailable",
                f"remote provider unavailable: {exc}",
            )
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
        headers={**_response_headers(upstream.headers), **ods_headers},
    )


@app.on_event("startup")
async def _startup() -> None:
    app.state.http = httpx.AsyncClient(follow_redirects=False, trust_env=False)
    app.state.direct_http_clients = {}


@app.on_event("shutdown")
async def _shutdown() -> None:
    await app.state.http.aclose()
    clients = getattr(app.state, "direct_http_clients", {})
    for client in clients.values():
        await client.aclose()
    clients.clear()
