from __future__ import annotations

from urllib.parse import urljoin

import httpx
from fastapi import Request, Response

from .audit import AuditLog
from .config import RouteRule


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
}


def _forward_headers(request: Request) -> dict[str, str]:
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }


def _response_headers(response: httpx.Response) -> dict[str, str]:
    return {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }


async def proxy_request(
    request: Request,
    rule: RouteRule,
    client: httpx.AsyncClient,
    audit: AuditLog,
) -> Response:
    body = await request.body()
    upstream_url = str(rule.upstream)
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    upstream = await client.request(
        request.method,
        upstream_url,
        content=body,
        headers=_forward_headers(request),
    )

    audit.write(
        "paid_request_forwarded",
        {
            "method": request.method,
            "path": request.url.path,
            "upstream": str(rule.upstream),
            "status_code": upstream.status_code,
        },
    )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_response_headers(upstream),
        media_type=upstream.headers.get("content-type"),
    )
