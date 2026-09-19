"""Bound owner JSON uploads before forwarding any host-agent operation."""

import asyncio

from fastapi import HTTPException, Request

BODY_TIMEOUT_SECONDS = 10


async def read_bounded_body(request: Request, limit: int, too_large: str,
                            *, headers: dict[str, str] | None = None) -> bytes:
    async def read() -> bytes:
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > limit:
                raise HTTPException(413, too_large, headers=headers)
            raw.extend(chunk)
        return bytes(raw)

    try:
        return await asyncio.wait_for(read(), timeout=BODY_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise HTTPException(408, "Request body timed out", headers={"Cache-Control": "no-store"}) from None
