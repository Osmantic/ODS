"""Owner-authenticated assistant display identity, independent of runtime Apply."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from host_agent_client import AgentHTTPError, AgentProtocolError, AgentUnavailable
from host_agent_client import async_request_json as request_agent_json
from portal_identity_contract import normalize_document, normalize_edit
from security import verify_api_key

from routers.pixel_providers import _check_depth, _constant, _float, _pairs

router = APIRouter(tags=["portal-identity"])
NO_STORE = {"Cache-Control": "no-store"}


async def _body(request):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 2048:
            raise HTTPException(413, "Assistant identity request is too large", headers=NO_STORE)
        raw.extend(chunk)
    try:
        text = bytes(raw).decode("utf-8")
        _check_depth(text)
        value = json.loads(text, object_pairs_hook=_pairs, parse_float=_float, parse_constant=_constant)
        return normalize_edit(value)
    except (ValueError, RecursionError):
        raise HTTPException(400, "Invalid assistant identity request", headers=NO_STORE) from None


async def _request(method, payload=None):
    path = "/v1/pixel/identity/save" if method == "POST" else "/v1/pixel/identity"
    try:
        result = await request_agent_json(method, path, payload=payload, timeout=10)
    except AgentHTTPError as error:
        status = error.status_code if error.status_code in (400, 409, 413, 503) else 502
        detail = ("Assistant identity changed elsewhere; refresh before saving." if status == 409
                  else "Assistant identity request failed; refresh before saving again.")
        raise HTTPException(status, detail, headers=NO_STORE) from None
    except AgentUnavailable:
        raise HTTPException(503, "Assistant identity outcome is unconfirmed; refresh before saving again.", headers=NO_STORE) from None
    except AgentProtocolError:
        raise HTTPException(502, "Invalid assistant identity response", headers=NO_STORE) from None
    try:
        result = normalize_document(result)
        if payload is not None and (result["revision"] != payload["expectedRevision"] + 1
                                    or result["displayName"] != payload["displayName"]):
            raise ValueError("identity-save-mismatch")
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(502, "Assistant identity save/readback is unconfirmed; refresh before saving again.", headers=NO_STORE) from None
    return JSONResponse(result, headers=NO_STORE)


@router.get("/api/pixel/identity")
async def get_identity(_key: str = Depends(verify_api_key)):
    return await _request("GET")


@router.post("/api/pixel/identity/save")
async def save_identity(request: Request, _key: str = Depends(verify_api_key)):
    return await _request("POST", await _body(request))
