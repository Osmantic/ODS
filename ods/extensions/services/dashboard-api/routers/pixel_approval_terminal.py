"""Human browser channel for one fixed Operations approval PTY.

Never registered as a model tool. Input/output are private, ephemeral and must
not be included in request logs, model context or error diagnostics.
"""

import asyncio
import hashlib
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from host_agent_client import async_request_json, AgentClientError
from routers.dashboard_session import SESSION_COOKIE_NAME, session_is_valid
from security import verify_api_key

router = APIRouter(
    prefix="/api/pixel/approval-terminal", tags=["pixel-approval-terminal"]
)


def browser_owner(request):
    cookie = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not session_is_valid(cookie):
        raise HTTPException(
            401,
            "Sign in to the Dashboard before opening the approval terminal.",
            headers={"x-ods-sign-in": "required"},
        )
    try:
        origin = urlsplit(request.headers.get("origin", ""))
    except ValueError:
        raise HTTPException(403, "Invalid browser origin.") from None
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    if (
        request.headers.get("sec-fetch-site") != "same-origin"
        or origin.netloc != request.headers.get("host")
        or origin.scheme != scheme
        or origin.path
        or origin.query
        or origin.fragment
        or origin.username
        or (
            scheme != "https"
            and origin.hostname not in {"localhost", "127.0.0.1", "::1"}
        )
    ):
        raise HTTPException(
            403,
            "Sign in to the Dashboard on HTTPS or this computer's loopback address before opening the approval terminal.",
        )
    return hashlib.sha256(cookie.encode()).hexdigest()


@router.post("", dependencies=[Depends(verify_api_key)])
async def approval_terminal(request: Request):
    owner = browser_owner(request)
    data = bytearray()
    try:
        async with asyncio.timeout(5):
            async for part in request.stream():
                data.extend(part)
                if len(data) > 4096:
                    raise ValueError()
        body = json.loads(data)
        if not isinstance(body, dict) or "owner" in body:
            raise ValueError()
        action = body.get("action")
        fields = (
            {"action", "job", "plan"} if action == "start" else {"action", "session"}
        )
        if action == "poll":
            fields.add("cursor")
        if action == "input":
            fields |= {"sequence", "line"}
        if action not in {"start", "poll", "input", "cancel"} or set(body) != fields:
            raise ValueError()
    except Exception:
        raise HTTPException(400, "Invalid approval terminal request.") from None
    try:
        receipt = await async_request_json(
            "POST",
            "/v1/pixel/approval-terminal",
            payload={**body, "owner": owner},
            timeout=10,
        )
    except AgentClientError:
        raise HTTPException(
            503,
            "The approval terminal could not confirm this request. No approval is inferred; check the protected job status.",
        ) from None
    return JSONResponse(
        receipt, headers={"Cache-Control": "no-store", "Pragma": "no-cache"}
    )
