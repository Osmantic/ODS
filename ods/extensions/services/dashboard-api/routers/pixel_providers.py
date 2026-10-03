"""Owner-authenticated, bounded proxy for Pixel-specific provider Settings."""

from __future__ import annotations

import json
import math

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from host_agent_client import (
    AgentHTTPError,
    AgentProtocolError,
    AgentUnavailable,
)
from host_agent_client import (
    async_request_json as request_agent_json,
)
from pixel_connection_public import normalize_connection_result
from pixel_provider_public import normalize_public
from pixel_provider_runtime_public import (
    normalize_change,
    normalize_outcome,
    normalize_runtime,
    safe_reason,
)
from security import verify_api_key

router = APIRouter(tags=["pixel-providers"])
MAX_BYTES = 256 * 1024
NO_STORE = {"Cache-Control": "no-store"}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-key")
        result[key] = value
    return result


def _float(raw):
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("nonfinite-number")
    return value


def _constant(_value):
    raise ValueError("nonfinite-number")


def _check_depth(text):
    # Limit parser nesting before json.loads, including on builds with a high
    # recursion limit. Brackets inside escaped JSON strings do not count.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 32:
                raise ValueError("document-too-deep")
        elif char in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("invalid-nesting")


async def _body(request, *, runtime=False):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > (2048 if runtime else MAX_BYTES):
            raise HTTPException(413, "Provider request exceeds size limit", headers=NO_STORE)
        raw.extend(chunk)
    try:
        text = bytes(raw).decode("utf-8")
        _check_depth(text)
        value = json.loads(text, object_pairs_hook=_pairs,
                           parse_float=_float, parse_constant=_constant)
        if runtime:
            return normalize_change(value)
    except (ValueError, RecursionError):
        raise HTTPException(400, "Invalid provider configuration request", headers=NO_STORE) from None
    if (not isinstance(value, dict)
            or not {"expectedRevision", "document"} <= set(value) <= {"expectedRevision", "document", "credentialChanges"}
            or type(value["expectedRevision"]) is not int
            or not 0 <= value["expectedRevision"] < 2**53 - 1
            or not isinstance(value["document"], dict)
            or not isinstance(value.get("credentialChanges", {}), dict)
            or len(value.get("credentialChanges", {})) > 32):
        raise HTTPException(400, "Invalid provider configuration request", headers=NO_STORE)
    return value


async def _request(method, path, payload=None):
    try:
        raw = await request_agent_json(method, path, payload=payload, timeout=10)
    except AgentHTTPError as exc:
        code = exc.status_code if exc.status_code in (400, 409, 413, 503) else 502
        raise HTTPException(code, "Provider settings request failed", headers=NO_STORE) from None
    except AgentUnavailable:
        raise HTTPException(503, "Provider settings are unavailable", headers=NO_STORE) from None
    except AgentProtocolError:
        raise HTTPException(502, "Invalid provider settings response", headers=NO_STORE) from None
    try:
        configuration = normalize_public(raw)
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(502, "Invalid provider settings response", headers=NO_STORE) from None
    return JSONResponse(content={
        "configuration": configuration,
        # Deliberately distinct from desired configuration.enabled. Persistence
        # alone must never claim an effective inference route or grant access.
        "runtime": {"status": "not-inspected", "reason": "runtime-status-separate"},
    }, headers=NO_STORE)


@router.get("/api/pixel/providers")
async def get_providers(_key: str = Depends(verify_api_key)):
    return await _request("GET", "/v1/pixel/providers")


@router.post("/api/pixel/providers/save")
async def save_providers(request: Request, _key: str = Depends(verify_api_key)):
    return await _request("POST", "/v1/pixel/providers/save", await _body(request))


@router.post("/api/pixel/providers/connection-probe")
async def probe_connection(request: Request, _key: str = Depends(verify_api_key)):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 65536:
            raise HTTPException(413, "Connection request exceeds size limit", headers=NO_STORE)
        raw.extend(chunk)
    try:
        text = bytes(raw).decode('utf-8')
        _check_depth(text)
        value = json.loads(text, object_pairs_hook=_pairs, parse_float=_float, parse_constant=_constant)
        if (type(value) is not dict or set(value) != {'bundle', 'confirmedEndpoint'}
                or type(value['bundle']) is not str or len(value['bundle'].encode('utf-8')) > 32768
                or type(value['confirmedEndpoint']) is not str
                or not 1 <= len(value['confirmedEndpoint']) <= 2048):
            raise ValueError('invalid-request')
        # Preserve original bundle text so the host also rejects duplicate keys.
        _check_depth(value['bundle'])
        bundle = json.loads(value['bundle'], object_pairs_hook=_pairs,
                            parse_float=_float, parse_constant=_constant)
        if type(bundle) is not dict:
            raise ValueError('invalid-request')
    except (ValueError, RecursionError):
        raise HTTPException(400, "Invalid connection request", headers=NO_STORE) from None
    try:
        result = await request_agent_json('POST', '/v1/pixel/providers/connection-probe',
                                          payload=value, timeout=25)
        result = normalize_connection_result(result)
        if (result['endpoint'] != value['confirmedEndpoint']
                or result['deviceId'] != bundle.get('deviceId')
                or result['expiresAt'] != bundle.get('expiresAt')
                or result['expected'] != bundle.get('expected')):
            raise ValueError('connection-identity-mismatch')
    except AgentHTTPError as error:
        status = error.status_code if error.status_code in (400, 409, 413, 503) else 502
        raise HTTPException(status, "Connection metadata could not be verified", headers=NO_STORE) from None
    except AgentUnavailable:
        raise HTTPException(503, "Connection inspection unavailable", headers=NO_STORE) from None
    except (AgentProtocolError, ValueError, TypeError, RecursionError):
        raise HTTPException(502, "Invalid connection inspection response", headers=NO_STORE) from None
    return JSONResponse(content=result, headers=NO_STORE)


def _runtime_failure_reason(error):
    """Bound the host envelope and retain only an exact public reason code."""
    text = error.response_text
    if type(text) is not str or len(text) > 2048:
        return None
    try:
        if len(text.encode("utf-8")) > 2048:
            return None
        _check_depth(text)
        value = json.loads(text, object_pairs_hook=_pairs,
                           parse_float=_float, parse_constant=_constant)
    except (ValueError, RecursionError):
        return None
    if (type(value) is not dict or set(value) != {"error", "code"}
            or type(value["error"]) is not str):
        return None
    return safe_reason(value["code"], fallback=None)


async def _runtime_request(method, payload=None):
    headers = NO_STORE
    try:
        raw = await request_agent_json(method, "/v1/pixel/providers/runtime", payload=payload,
                                       timeout=340 if method == "POST" else 65)
    except AgentHTTPError as error:
        status = error.status_code if error.status_code in (400, 409, 413, 503) else 502
        reason = _runtime_failure_reason(error) if method == "POST" else None
        if reason is not None:
            raise HTTPException(status, {
                "reason": reason,
                "message": "Provider controller reported a problem; refresh runtime status before any further change.",
            }, headers=headers) from None
        raise HTTPException(status, "Provider runtime request failed; inspect before retrying", headers=headers) from None
    except AgentUnavailable:
        raise HTTPException(503, "Provider runtime is unavailable; inspect before retrying", headers=headers) from None
    except AgentProtocolError:
        raise HTTPException(502, "Provider runtime result is unconfirmed; inspect before retrying", headers=headers) from None
    try:
        result = normalize_outcome(raw, payload) if method == "POST" else normalize_runtime(raw)
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(502, "Invalid provider runtime response; inspect before retrying", headers=headers) from None
    return JSONResponse(content=result, headers=headers)


@router.get("/api/pixel/providers/runtime")
async def get_provider_runtime(_key: str = Depends(verify_api_key)):
    return await _runtime_request("GET")


@router.post("/api/pixel/providers/runtime")
async def change_provider_runtime(request: Request, _key: str = Depends(verify_api_key)):
    return await _runtime_request("POST", await _body(request, runtime=True))

@router.get("/api/pixel/providers/health")
async def get_active_provider_health(_key: str = Depends(verify_api_key)):
    """Background or on-demand probe of the active provider's health."""
    try:
        raw = await request_agent_json("GET", "/v1/pixel/providers/health", timeout=12)
        if type(raw) is not dict:
            raise ValueError()
        if raw.get("status") == "online":
            if (set(raw) != {"status", "models"} or type(raw["models"]) is not int
                    or not 1 <= raw["models"] <= 4096):
                raise ValueError()
        elif set(raw) != {"status"} or raw["status"] not in ("offline", "unavailable", "inactive"):
            raise ValueError()
        return JSONResponse(content=raw, headers=NO_STORE)
    except (AgentHTTPError, AgentUnavailable, AgentProtocolError, ValueError, TypeError):
        return JSONResponse(content={"status": "unavailable"}, headers=NO_STORE)

