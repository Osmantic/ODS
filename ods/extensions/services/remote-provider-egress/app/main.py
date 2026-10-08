"""ODS remote-provider egress service.

The service is internal-only. It accepts the OpenAI-compatible paths LiteLLM
uses, validates generated route state against the shared policy contract, and
injects provider credentials from a private file at the final egress boundary.

Only LiteLLM and dashboard-api share its network, and every request except the
status reads must carry the LiteLLM gateway key, so a container that can reach
the service still cannot spend the provider key without it.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
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
    connection_header_names,
    load_route_state,
    prepare_upstream_request,
    provider_secret_status,
    read_provider_secret,
    route_from_state,
    validate_direct_provider_resolution,
)
from remote_provider.egress_probe import probe_route_response
from remote_provider.telemetry import CompletionObservation, route_fingerprint
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
logger = logging.getLogger("ods-remote-provider-egress")

MAX_BODY_BYTES = int(
    os.environ.get("ODS_REMOTE_PROVIDER_MAX_BODY_BYTES", str(DEFAULT_MAX_BODY_BYTES))
)
UPSTREAM_TIMEOUT_SECONDS = float(
    os.environ.get("ODS_REMOTE_PROVIDER_UPSTREAM_TIMEOUT", "600")
)
# Direct-provider HTTP clients kept open, one per provider endpoint.
MAX_DIRECT_HTTP_CLIENTS = 4
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
# The LiteLLM gateway key. Anyone holding it can already reach the provider
# through LiteLLM, so requiring it here admits no new caller.
CALLER_KEY = os.environ.get("ODS_REMOTE_PROVIDER_CALLER_KEY", "")
# Status reads stay open for the container healthcheck and dashboard-api's
# service poller; they carry no provider credential and spend nothing.
_OPEN_ROUTES = frozenset({("GET", "/health"), ("HEAD", "/health"), ("GET", "/telemetry")})
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
    state = load_route_state(ROUTE_PATH)
    route = route_from_state(state, policy=policy)
    route['routeFingerprint'] = route_fingerprint(state)
    return route


# Clients closing in the background, referenced until they finish.
_closing_clients: set[asyncio.Task] = set()


def _close_later(client: httpx.AsyncClient) -> None:
    task = asyncio.get_running_loop().create_task(client.aclose())
    _closing_clients.add(task)
    task.add_done_callback(_closing_clients.discard)


def _http_client(connection_key: str = "") -> httpx.AsyncClient:
    if connection_key:
        clients = getattr(app.state, "direct_http_clients", None)
        if clients is None:
            clients = {}
            app.state.direct_http_clients = clients
        # Least recently used first: a reused endpoint moves to the end.
        client = clients.pop(connection_key, None)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
        clients[connection_key] = client
        # Endpoints change only when the operator reconfigures the remote
        # provider. Keep the most recent ones and close the rest, rather than
        # holding every past endpoint's connection pool forever (#2701).
        while len(clients) > MAX_DIRECT_HTTP_CLIENTS:
            _close_later(clients.pop(next(iter(clients))))
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


def _caller_rejection(method: str, path: str, authorization: str) -> JSONResponse | None:
    """Refuse a caller that does not present the gateway key."""
    if (method.upper(), path) in _OPEN_ROUTES:
        return None
    if not CALLER_KEY:
        return _error_response(EgressError(
            503, "missing_caller_key",
            "remote provider egress has no caller key; rerun the ODS installer",
        ))
    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
            presented.strip().encode("utf-8"), CALLER_KEY.encode("utf-8")):
        response = _error_response(EgressError(
            401, "caller_unauthorized", "remote provider egress requires the gateway key",
        ))
        response.headers["WWW-Authenticate"] = "Bearer"
        return response
    return None


@app.middleware("http")
async def _require_caller_key(request: Request, call_next):
    rejection = _caller_rejection(
        request.method, request.url.path, request.headers.get("authorization", ""))
    if rejection is not None:
        return rejection
    return await call_next(request)


def _response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    excluded = _HOP_BY_HOP_RESPONSE_HEADERS | connection_header_names(headers)
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in excluded
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
    if not CALLER_KEY:
        return {
            "status": "degraded",
            "ready": False,
            "reason": "missing_caller_key",
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
        # The probe makes blocking HTTP calls (urllib) for up to its timeout.
        # Run it on a worker thread so inference requests and streams keep
        # being served meanwhile.
        payload = await asyncio.to_thread(
            probe_route_response,
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


@app.get('/telemetry')
async def completion_telemetry() -> Response:
    try:
        route = _load_route()
    except EgressError:
        return JSONResponse({'sample': None}, headers={'Cache-Control': 'no-store'})
    sample = getattr(app.state, 'completion_sample', None)
    if not route.get('enabled') or not sample or sample['routeFingerprint'] != route.get('routeFingerprint'):
        sample = None
    return JSONResponse({'sample': sample}, headers={'Cache-Control': 'no-store'})


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
            headers=request.headers,
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
    observation = CompletionObservation(route)
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
                        if 200 <= upstream.status_code < 300:
                            observation.feed(chunk)
                        yield chunk
                    sample = observation.result()
                    if sample:
                        app.state.completion_sample = sample
                finally:
                    await upstream.aclose()

            response_headers = _response_headers(upstream.headers)
            return StreamingResponse(
                stream_body(),
                status_code=upstream.status_code,
                media_type=response_headers.get("content-type", "text/event-stream"),
                headers={**response_headers, **ods_headers},
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
        logger.warning("remote provider unavailable: %s", exc)
        return _error_response(
            EgressError(502, "upstream_unavailable", "remote provider unavailable")
        )
    response_headers = _response_headers(upstream.headers)
    if 200 <= upstream.status_code < 300 and len(upstream.content) <= 16 * 1024 * 1024:
        try:
            observation.payload(upstream.json())
            observation.complete = True
            sample = observation.result()
            if sample:
                app.state.completion_sample = sample
        except (ValueError, RecursionError):
            # A provider body that is not bounded JSON is not a usable sample.
            observation.invalid = True
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=response_headers.get("content-type", "application/json"),
        headers={**response_headers, **ods_headers},
    )


@app.on_event("startup")
async def _startup() -> None:
    app.state.completion_sample = None
    app.state.http = httpx.AsyncClient(follow_redirects=False, trust_env=False)
    app.state.direct_http_clients = {}


@app.on_event("shutdown")
async def _shutdown() -> None:
    await app.state.http.aclose()
    clients = getattr(app.state, "direct_http_clients", {})
    for client in clients.values():
        await client.aclose()
    clients.clear()
