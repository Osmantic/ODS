"""Shared, bounded HTTP transport for the ODS host agent."""

from __future__ import annotations

import asyncio
import errno
import json
import threading
import time
from typing import Any

import httpx

from config import AGENT_URL, ODS_AGENT_KEY


class AgentClientError(RuntimeError):
    """Base error for host-agent transport failures."""


class AgentHTTPError(AgentClientError):
    """The host agent returned a non-success HTTP status."""

    def __init__(self, status_code: int, detail: str, response_text: str = "") -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.response_text = response_text


class AgentUnavailable(AgentClientError):
    """The host agent could not be reached."""


class AgentTimeout(AgentUnavailable):
    """The host-agent request exceeded an explicit timeout."""


class AgentProtocolError(AgentClientError):
    """The host agent returned a response that violated its JSON contract."""


_SYNC_LIMITS = httpx.Limits(
    max_connections=8,
    max_keepalive_connections=4,
    keepalive_expiry=30.0,
)
_ASYNC_LIMITS = httpx.Limits(
    max_connections=2,
    max_keepalive_connections=2,
    keepalive_expiry=30.0,
)
_sync_client: httpx.Client | None = None
_async_client: httpx.AsyncClient | None = None
_sync_client_lock = threading.Lock()
_async_client_lock = threading.Lock()

# Docker Desktop can briefly withdraw the synthetic host.docker.internal route
# while native model operations are changing container state.  A connect error
# is safe to retry even for POST because no connection was established and no
# request bytes reached the host agent.  Read/write/protocol failures remain
# non-retryable for POST so model activations and extension operations cannot be
# duplicated after the agent may have accepted them.
_CONNECT_RETRY_DELAYS_SECONDS = (0.25, 1.0, 3.0, 6.0)
_TRANSIENT_ROUTE_ERRNOS = {
    errno.ENETUNREACH,
    errno.EHOSTUNREACH,
    10051,  # WSAENETUNREACH
    10065,  # WSAEHOSTUNREACH
}
_TRANSIENT_ROUTE_MESSAGES = (
    "network is unreachable",
    "no route to host",
)


def _is_transient_route_connect_error(exc: BaseException) -> bool:
    """Return True only for a pre-connect host-route withdrawal.

    Connection refused, DNS errors, and generic mocked ConnectErrors still
    fail immediately.  The bounded retry is reserved for Docker Desktop's
    observed ENETUNREACH/EHOSTUNREACH route flap.
    """
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "errno", None) in _TRANSIENT_ROUTE_ERRNOS:
            return True
        message = str(current).casefold()
        if any(token in message for token in _TRANSIENT_ROUTE_MESSAGES):
            return True
        current = current.__cause__ or current.__context__
    return False


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ODS_AGENT_KEY}"}


def _timeout(read_seconds: float) -> httpx.Timeout:
    return httpx.Timeout(
        connect=5.0,
        read=max(0.1, float(read_seconds)),
        write=30.0,
        pool=5.0,
    )


def _get_sync_client() -> httpx.Client:
    global _sync_client

    client = _sync_client
    if client is not None and not client.is_closed:
        return client
    with _sync_client_lock:
        client = _sync_client
        if client is None or client.is_closed:
            _sync_client = httpx.Client(
                base_url=AGENT_URL,
                headers=_headers(),
                limits=_SYNC_LIMITS,
                timeout=_timeout(5.0),
                trust_env=False,
            )
        return _sync_client


async def _get_async_client() -> httpx.AsyncClient:
    global _async_client

    client = _async_client
    if client is not None and not client.is_closed:
        return client
    with _async_client_lock:
        client = _async_client
        if client is None or client.is_closed:
            _async_client = httpx.AsyncClient(
                base_url=AGENT_URL,
                headers=_headers(),
                limits=_ASYNC_LIMITS,
                timeout=_timeout(5.0),
                trust_env=False,
            )
        return _async_client


def _error_detail(response: httpx.Response) -> tuple[str, str]:
    text = response.text
    detail: Any = None
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("error") or payload.get("detail")
    except ValueError:
        pass
    if isinstance(detail, (dict, list)):
        detail = json.dumps(detail, separators=(",", ":"))
    if not isinstance(detail, str) or not detail.strip():
        detail = f"Host agent returned HTTP {response.status_code}"
    return detail, text


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    detail, text = _error_detail(response)
    raise AgentHTTPError(response.status_code, detail, text)


def _decode_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise AgentProtocolError("Host agent returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise AgentProtocolError("Host agent returned non-object JSON")
    return payload


def _sync_request(
    method: str,
    path: str,
    *,
    payload: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float,
) -> httpx.Response:
    method = method.upper()
    stale_connection_retries = 1 if method == "GET" else 0
    connect_retry_index = 0
    while True:
        try:
            return _get_sync_client().request(
                method,
                path,
                json=payload if payload is not None else None,
                params=params,
                timeout=_timeout(timeout),
            )
        except httpx.TimeoutException as exc:
            raise AgentTimeout(f"Host agent {method} {path} timed out") from exc
        except httpx.ConnectError as exc:
            if (
                _is_transient_route_connect_error(exc)
                and connect_retry_index < len(_CONNECT_RETRY_DELAYS_SECONDS)
            ):
                time.sleep(_CONNECT_RETRY_DELAYS_SECONDS[connect_retry_index])
                connect_retry_index += 1
                continue
            raise AgentUnavailable(f"Host agent {method} {path} is unreachable: {exc}") from exc
        except (httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            if stale_connection_retries:
                stale_connection_retries -= 1
                continue
            raise AgentUnavailable(f"Host agent {method} {path} is unreachable: {exc}") from exc
        except httpx.RequestError as exc:
            raise AgentUnavailable(f"Host agent {method} {path} is unreachable: {exc}") from exc


async def _async_request(
    method: str,
    path: str,
    *,
    payload: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float,
) -> httpx.Response:
    method = method.upper()
    stale_connection_retries = 1 if method == "GET" else 0
    connect_retry_index = 0
    while True:
        try:
            client = await _get_async_client()
            return await client.request(
                method,
                path,
                json=payload if payload is not None else None,
                params=params,
                timeout=_timeout(timeout),
            )
        except httpx.TimeoutException as exc:
            raise AgentTimeout(f"Host agent {method} {path} timed out") from exc
        except httpx.ConnectError as exc:
            if (
                _is_transient_route_connect_error(exc)
                and connect_retry_index < len(_CONNECT_RETRY_DELAYS_SECONDS)
            ):
                await asyncio.sleep(_CONNECT_RETRY_DELAYS_SECONDS[connect_retry_index])
                connect_retry_index += 1
                continue
            raise AgentUnavailable(f"Host agent {method} {path} is unreachable: {exc}") from exc
        except (httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            if stale_connection_retries:
                stale_connection_retries -= 1
                continue
            raise AgentUnavailable(f"Host agent {method} {path} is unreachable: {exc}") from exc
        except httpx.RequestError as exc:
            raise AgentUnavailable(f"Host agent {method} {path} is unreachable: {exc}") from exc


def request_json(
    method: str,
    path: str,
    *,
    payload: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    response = _sync_request(method, path, payload=payload, params=params, timeout=timeout)
    _raise_for_status(response)
    return _decode_json(response)


def request_text(
    method: str,
    path: str,
    *,
    payload: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> str:
    response = _sync_request(method, path, payload=payload, params=params, timeout=timeout)
    _raise_for_status(response)
    return response.text


async def async_request_json(
    method: str,
    path: str,
    *,
    payload: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    response = await _async_request(
        method, path, payload=payload, params=params, timeout=timeout
    )
    _raise_for_status(response)
    return _decode_json(response)


async def async_request_text(
    method: str,
    path: str,
    *,
    payload: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> str:
    response = await _async_request(
        method, path, payload=payload, params=params, timeout=timeout
    )
    _raise_for_status(response)
    return response.text


async def shutdown_clients() -> None:
    """Close both process-wide pools during FastAPI shutdown."""
    global _sync_client, _async_client

    with _sync_client_lock:
        sync_client, _sync_client = _sync_client, None
    with _async_client_lock:
        async_client, _async_client = _async_client, None
    if sync_client is not None and not sync_client.is_closed:
        await asyncio.to_thread(sync_client.close)
    if async_client is not None and not async_client.is_closed:
        await async_client.aclose()
