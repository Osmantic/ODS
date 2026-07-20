"""ODS model-router: the stable-alias data plane (Switchboard PR 3).

One job: requests arrive for the public alias (``ods/current`` or a
compatibility alias), and the router forwards them to the concrete backend
and runtime model named by the host agent's ``model-state.json`` — rewriting
the request ``model`` on the way in and restoring the requested alias on the
way out (including every SSE chunk).

Security boundary (plan §3.6):
- Internal Compose service only; no host port. Only the explicit OpenAI
  paths below are forwarded; everything else is rejected.
- ``endpointId`` resolves through a read-only allowlist file generated at
  install; state can never name an arbitrary upstream.
- Hop-by-hop and client authorization headers are stripped; backend
  credentials come from the router's own environment.
- Bounded body size, queue depth, and timeouts, all covered by tests.

Route evidence (plan §3.4): bounded in-memory records correlated by a
signed probe marker, exposed only on the internal network with a bearer
key. No prompts, generations, or credentials are ever stored.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("ods-model-router")

PUBLIC_ALIASES = ("ods/current", "default")
STATE_PATH = Path(os.environ.get("ODS_MODEL_STATE_PATH", "/state/model-state.json"))
ENDPOINTS_PATH = Path(
    os.environ.get("ODS_ROUTER_ENDPOINTS_PATH", "/config/endpoints.json")
)
INTERNAL_KEY = os.environ.get("ODS_ROUTER_INTERNAL_KEY", "")
PROBE_KEY = os.environ.get("ODS_FLEET_PROBE_KEY", "")

MAX_BODY_BYTES = int(os.environ.get("ODS_ROUTER_MAX_BODY_BYTES", str(2 * 1024 * 1024)))
MAX_QUEUE_DEPTH = int(os.environ.get("ODS_ROUTER_MAX_QUEUE_DEPTH", "64"))
QUEUE_WAIT_SECONDS = int(os.environ.get("ODS_ROUTER_QUEUE_WAIT_SECONDS", "60"))
UPSTREAM_TIMEOUT_SECONDS = float(os.environ.get("ODS_ROUTER_UPSTREAM_TIMEOUT", "600"))
EVIDENCE_LIMIT = 2048
EVIDENCE_TTL_SECONDS = 15 * 60

FORWARD_PATHS = {
    "/v1/chat/completions": "POST",
    "/v1/completions": "POST",
    "/v1/responses": "POST",
}

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "authorization",
    "content-length",
}

_PROBE_RE = re.compile(
    r"\[ODS_PROBE id=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) "
    r"sig=([A-Za-z0-9_-]+)\]"
)

app = FastAPI(title="ODS Model Router", docs_url=None, redoc_url=None,
              openapi_url=None)

_inflight = 0
_inflight_lock = asyncio.Lock()

_state_cache: dict[str, Any] = {"mtime": None, "doc": None}
_endpoints_cache: dict[str, Any] = {"loaded": False, "endpoints": {}}
_evidence: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


class RouterError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _load_endpoints() -> dict[str, dict[str, Any]]:
    """Startup-validated allowlist: endpointId -> {baseUrl, apiKeyEnv?}."""
    if _endpoints_cache["loaded"]:
        return _endpoints_cache["endpoints"]
    endpoints: dict[str, dict[str, Any]] = {}
    try:
        raw = json.loads(ENDPOINTS_PATH.read_text(encoding="utf-8"))
        for entry in raw.get("endpoints", []):
            endpoint_id = str(entry.get("id") or "")
            base_url = str(entry.get("baseUrl") or "")
            if not endpoint_id or not base_url.startswith(("http://", "https://")):
                continue
            endpoints[endpoint_id] = {
                "baseUrl": base_url.rstrip("/"),
                "apiKeyEnv": str(entry.get("apiKeyEnv") or ""),
            }
    except (OSError, ValueError) as exc:
        logger.error("endpoints allowlist unavailable: %s", exc)
    _endpoints_cache["endpoints"] = endpoints
    _endpoints_cache["loaded"] = True
    return endpoints


def _read_state() -> dict[str, Any] | None:
    """Sequence-aware cached read of the host agent's state record."""
    try:
        stat = STATE_PATH.stat()
    except OSError:
        return _state_cache["doc"]
    mtime = (stat.st_mtime_ns, stat.st_size)
    if _state_cache["mtime"] == mtime:
        return _state_cache["doc"]
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _state_cache["doc"]
    if not isinstance(doc, dict) or doc.get("schema") != "ods.model-state.v1":
        return _state_cache["doc"]
    _state_cache["mtime"] = mtime
    _state_cache["doc"] = doc
    return doc


def _active_route() -> dict[str, Any]:
    doc = _read_state()
    active = (doc or {}).get("active")
    if not isinstance(active, dict):
        raise RouterError(503, "no_active_route",
                          "No verified active model route is available yet")
    endpoint_id = str(((active.get("backend") or {}).get("endpointId")) or "")
    endpoint = _load_endpoints().get(endpoint_id)
    if endpoint is None:
        raise RouterError(503, "endpoint_not_allowlisted",
                          f"Active endpointId {endpoint_id!r} is not in the "
                          "router allowlist")
    availability = (doc or {}).get("availability") or {}
    return {
        "routeSeq": int(active.get("routeSeq") or 0),
        "runtimeModelId": str(active.get("runtimeModelId") or ""),
        "backendKind": str((active.get("backend") or {}).get("kind") or "unknown"),
        "endpointId": endpoint_id,
        "baseUrl": endpoint["baseUrl"],
        "apiKeyEnv": endpoint["apiKeyEnv"],
        "queueMode": str(availability.get("mode") or "serve_active") == "queue",
    }


def _record_evidence(record: dict[str, Any]) -> None:
    now = time.monotonic()
    record["storedAt"] = now
    _evidence[record["probeId"]] = record
    while len(_evidence) > EVIDENCE_LIMIT:
        _evidence.popitem(last=False)
    stale = [k for k, v in _evidence.items()
             if now - v["storedAt"] > EVIDENCE_TTL_SECONDS]
    for key in stale:
        _evidence.pop(key, None)


def _verify_probe_marker(body_text: str) -> str | None:
    """Return the probe UUID only for exactly one validly signed marker."""
    if not PROBE_KEY:
        return None
    matches = _PROBE_RE.findall(body_text)
    if len(matches) != 1:
        return None
    probe_id, signature = matches[0]
    expected = base64.urlsafe_b64encode(
        hmac.new(PROBE_KEY.encode("utf-8"), probe_id.encode("utf-8"),
                 hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(expected, signature):
        return None
    return probe_id


def _sanitize_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in request.headers.items():
        if name.lower() in _HOP_BY_HOP:
            continue
        headers[name] = value
    headers["content-type"] = "application/json"
    return headers


def _rewrite_sse_lines(block: bytes, alias: str) -> bytes:
    """Stamp the requested alias into every complete SSE data line."""
    out_lines = []
    for line in block.split(b"\n"):
        if line.startswith(b"data:") and b"{" in line:
            payload = line[5:].strip()
            try:
                obj = json.loads(payload)
                if isinstance(obj, dict) and "model" in obj:
                    obj["model"] = alias
                    line = b"data: " + json.dumps(obj, separators=(",", ":")).encode("utf-8")
            except ValueError:
                pass
        out_lines.append(line)
    return b"\n".join(out_lines)


class _SSERewriter:
    """Alias-stamp an SSE stream whose chunks do not align with its lines.

    ``aiter_bytes()`` yields network-sized chunks; a ``data:`` line split
    across two of them is valid JSON in neither half, so rewriting per chunk
    silently passes the upstream's concrete model id through to the client.
    Hold an incomplete trailing line back until its newline arrives.

    ``_MAX_PENDING`` bounds the hold-back so an upstream that streams without
    newlines cannot grow the buffer without limit; past it the pending bytes
    are forwarded unmodified, which is exactly the old behaviour.
    """

    _MAX_PENDING = 1024 * 1024

    def __init__(self, alias: str) -> None:
        self._alias = alias
        self._pending = b""

    def feed(self, chunk: bytes) -> bytes:
        data = self._pending + chunk
        complete, newline, remainder = data.rpartition(b"\n")
        if not newline:
            if len(data) > self._MAX_PENDING:
                self._pending = b""
                return data
            self._pending = data
            return b""
        self._pending = remainder
        return _rewrite_sse_lines(complete, self._alias) + b"\n"

    def flush(self) -> bytes:
        """Emit a final line that arrived without a trailing newline."""
        if not self._pending:
            return b""
        remainder, self._pending = self._pending, b""
        return _rewrite_sse_lines(remainder, self._alias)


@app.get("/health")
async def health() -> dict[str, Any]:
    doc = _read_state()
    return {
        "status": "ok",
        "hasRoute": bool((doc or {}).get("active")),
        "seq": (doc or {}).get("seq"),
        "routeSeq": (doc or {}).get("routeSeq"),
    }


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    data = [{"id": alias, "object": "model", "owned_by": "ods"}
            for alias in PUBLIC_ALIASES]
    try:
        route = _active_route()
        metadata = {"routedModel": route["runtimeModelId"],
                    "backend": route["backendKind"],
                    "routeSeq": route["routeSeq"]}
    except RouterError:
        metadata = {"routedModel": None, "backend": None, "routeSeq": None}
    return {"object": "list", "data": data, "ods": metadata}


@app.get("/internal/route-evidence/{probe_id}")
async def route_evidence(probe_id: str, request: Request) -> Response:
    provided = request.headers.get("authorization", "")
    if not INTERNAL_KEY or provided != f"Bearer {INTERNAL_KEY}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    record = _evidence.get(probe_id)
    if record is None or time.monotonic() - record["storedAt"] > EVIDENCE_TTL_SECONDS:
        return JSONResponse({"error": "not_found"}, status_code=404)
    public = {k: v for k, v in record.items() if k != "storedAt"}
    return JSONResponse(public)


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE",
                                             "PATCH", "HEAD", "OPTIONS", "CONNECT"])
async def forward(full_path: str, request: Request) -> Response:
    path = "/" + full_path
    method = FORWARD_PATHS.get(path)
    if method is None or request.method != method:
        return JSONResponse(
            {"error": {"message": f"Path not served by the ODS model router: {path}",
                       "type": "not_forwarded", "code": "404"}},
            status_code=404,
        )

    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return JSONResponse(
            {"error": {"message": "Request body exceeds the router limit",
                       "type": "payload_too_large", "code": "413"}},
            status_code=413,
        )
    try:
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
    except (ValueError, UnicodeDecodeError) as exc:
        return JSONResponse(
            {"error": {"message": f"Invalid JSON body: {exc}",
                       "type": "invalid_request_error", "code": "400"}},
            status_code=400,
        )

    requested_alias = str(payload.get("model") or PUBLIC_ALIASES[0])

    global _inflight
    async with _inflight_lock:
        if _inflight >= MAX_QUEUE_DEPTH:
            return JSONResponse(
                {"error": {"message": "Router queue is full",
                           "type": "overloaded", "code": "503"}},
                status_code=503, headers={"Retry-After": "5"},
            )
        _inflight += 1
    try:
        return await _forward_inner(request, path, payload, requested_alias, body)
    finally:
        async with _inflight_lock:
            _inflight -= 1


async def _forward_inner(request: Request, path: str, payload: dict[str, Any],
                         requested_alias: str, raw_body: bytes) -> Response:
    deadline = time.monotonic() + QUEUE_WAIT_SECONDS
    while True:
        try:
            route = _active_route()
        except RouterError as exc:
            return JSONResponse(
                {"error": {"message": exc.message, "type": exc.code,
                           "code": str(exc.status)}},
                status_code=exc.status,
                headers={"Retry-After": "5"} if exc.status == 503 else {},
            )
        if not route["queueMode"]:
            break
        if time.monotonic() >= deadline:
            return JSONResponse(
                {"error": {"message": "A model swap is in progress",
                           "type": "model_swap_in_progress", "code": "503"}},
                status_code=503, headers={"Retry-After": "10"},
            )
        await asyncio.sleep(0.25)

    payload["model"] = route["runtimeModelId"]
    request_id = str(uuid.uuid4())
    probe_id = _verify_probe_marker(raw_body.decode("utf-8", "replace"))
    is_stream = bool(payload.get("stream"))

    headers = _sanitize_headers(request)
    api_key = os.environ.get(route["apiKeyEnv"], "") if route["apiKeyEnv"] else ""
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    ods_headers = {
        "X-ODS-Request-Id": request_id,
        "X-ODS-Requested-Model": requested_alias,
        "X-ODS-Routed-Model": route["runtimeModelId"],
        "X-ODS-Backend": route["backendKind"],
        "X-ODS-Route-Seq": str(route["routeSeq"]),
    }

    url = route["baseUrl"] + path
    client: httpx.AsyncClient = app.state.http
    evidence_base = {
        "probeId": probe_id,
        "requestedModel": requested_alias,
        "routedModel": route["runtimeModelId"],
        "backend": route["backendKind"],
        "endpointId": route["endpointId"],
        "routeSeq": route["routeSeq"],
        "path": path,
    }

    try:
        if is_stream:
            upstream_request = client.build_request(
                "POST", url, content=json.dumps(payload).encode("utf-8"),
                headers=headers, timeout=UPSTREAM_TIMEOUT_SECONDS,
            )
            upstream = await client.send(upstream_request, stream=True)
            lemonade_route = upstream.headers.get("x-lemonade-route")
            if lemonade_route:
                ods_headers["X-Lemonade-Route"] = lemonade_route
            if probe_id:
                _record_evidence({**evidence_base,
                                  "status": upstream.status_code,
                                  "responseModel": requested_alias,
                                  "lemonadeRoute": lemonade_route})

            async def stream_body() -> AsyncIterator[bytes]:
                rewriter = _SSERewriter(requested_alias)
                try:
                    async for chunk in upstream.aiter_bytes():
                        rewritten = rewriter.feed(chunk)
                        if rewritten:
                            yield rewritten
                    tail = rewriter.flush()
                    if tail:
                        yield tail
                finally:
                    await upstream.aclose()

            media_type = upstream.headers.get("content-type",
                                              "text/event-stream")
            return StreamingResponse(stream_body(),
                                     status_code=upstream.status_code,
                                     media_type=media_type,
                                     headers=ods_headers)

        upstream = await client.post(
            url, content=json.dumps(payload).encode("utf-8"), headers=headers,
            timeout=UPSTREAM_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        return JSONResponse(
            {"error": {"message": "Upstream model runtime timed out",
                       "type": "upstream_timeout", "code": "504"}},
            status_code=504, headers=ods_headers,
        )
    except httpx.HTTPError as exc:
        return JSONResponse(
            {"error": {"message": f"Upstream model runtime unavailable: {exc}",
                       "type": "upstream_unavailable", "code": "502"}},
            status_code=502, headers=ods_headers,
        )

    lemonade_route = upstream.headers.get("x-lemonade-route")
    if lemonade_route:
        ods_headers["X-Lemonade-Route"] = lemonade_route

    response_model = None
    content = upstream.content
    try:
        parsed = json.loads(content.decode("utf-8"))
        if isinstance(parsed, dict):
            response_model = parsed.get("model")
            if "model" in parsed:
                parsed["model"] = requested_alias
            lemonade_meta = parsed.get("x_lemonade_route")
            if lemonade_meta is not None:
                ods_headers.setdefault("X-Lemonade-Route",
                                       json.dumps(lemonade_meta)
                                       if not isinstance(lemonade_meta, str)
                                       else lemonade_meta)
            content = json.dumps(parsed).encode("utf-8")
    except (ValueError, UnicodeDecodeError):
        pass

    if probe_id:
        _record_evidence({**evidence_base,
                          "status": upstream.status_code,
                          "responseModel": str(response_model or ""),
                          "lemonadeRoute": lemonade_route})

    media_type = upstream.headers.get("content-type", "application/json")
    return Response(content=content, status_code=upstream.status_code,
                    media_type=media_type, headers=ods_headers)


@app.on_event("startup")
async def _startup() -> None:
    app.state.http = httpx.AsyncClient(follow_redirects=False)
    _load_endpoints()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await app.state.http.aclose()
