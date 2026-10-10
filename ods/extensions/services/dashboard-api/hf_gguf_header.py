"""Read a Hugging Face GGUF's metadata header with HTTP Range requests.

Only the metadata block is fetched (about 1-16 MiB for real models, capped at
32 MiB), never tensor data. Hugging Face counts every GET or HEAD on a GGUF
as a download, so callers read one file per repository a user opens, never
for search results.
"""

from __future__ import annotations

import struct
import threading
from collections import OrderedDict
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx

from gguf_inspector import GGUFTruncated, NotGGUF, parse_gguf_metadata

HF_BASE = "https://huggingface.co"
FIRST_READ_BYTES = 2 * 1024 * 1024
MAX_READ_BYTES = 32 * 1024 * 1024
_CACHE_MAX_ENTRIES = 64
_CACHE: "OrderedDict[tuple[str, str, str], dict[str, Any]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class HeaderUnavailable(Exception):
    """The header could not be read. ``code`` names the reason in one word."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def redirect_allowed(url: str) -> bool:
    """HTTPS on a Hugging Face domain (the CDN redirect is *.hf.co)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (
        host in {"hf.co", "huggingface.co"}
        or host.endswith(".hf.co")
        or host.endswith(".huggingface.co")
    )


def _raise_for_hub_status(response: httpx.Response) -> None:
    status = response.status_code
    if status in {401, 403}:
        if response.headers.get("x-error-code", "").lower() == "gatedrepo":
            raise HeaderUnavailable(
                "gated",
                "This repository is gated: accept its license on Hugging Face and set HF_TOKEN",
            )
        raise HeaderUnavailable("unavailable", "This repository is private or no longer available")
    if status == 404:
        raise HeaderUnavailable("not_found", "The file is no longer in this repository revision")
    if status == 429:
        raise HeaderUnavailable("rate_limited", "Hugging Face rate limit reached; retry later or set HF_TOKEN")
    if status >= 400:
        raise HeaderUnavailable("upstream_error", f"Hugging Face returned HTTP {status}")


async def _resolve(
    client: httpx.AsyncClient,
    repo_id: str,
    revision: str,
    filename: str,
    expected_size: int | None,
    token: str,
) -> str:
    url = (
        f"{HF_BASE}/{quote(repo_id, safe='/')}/resolve/"
        f"{quote(revision, safe='')}/{quote(filename, safe='/')}"
    )
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = await client.head(url, headers=headers)
    except httpx.TimeoutException as exc:
        raise HeaderUnavailable("unreachable", "Hugging Face did not respond in time") from exc
    except httpx.HTTPError as exc:
        raise HeaderUnavailable("unreachable", "Hugging Face could not be reached") from exc
    _raise_for_hub_status(response)
    linked_size = response.headers.get("x-linked-size", "")
    if expected_size and linked_size.isdigit() and int(linked_size) != expected_size:
        raise HeaderUnavailable("size_mismatch", "The file size on Hugging Face does not match its listing")
    if response.status_code in _REDIRECT_STATUSES:
        location = urljoin(url, response.headers.get("location", ""))
        if not redirect_allowed(location):
            raise HeaderUnavailable("redirect_refused", "Hugging Face redirected to an unexpected host")
        return location
    if response.status_code == 200:
        return url
    raise HeaderUnavailable("upstream_error", f"Hugging Face returned HTTP {response.status_code}")


async def _read_range(client: httpx.AsyncClient, url: str, start: int, end: int) -> bytes:
    """Return bytes ``start..end`` inclusive; never stream a whole model file."""
    limit = end - start + 1
    try:
        async with client.stream("GET", url, headers={"Range": f"bytes={start}-{end}"}) as response:
            if response.status_code != 206:
                _raise_for_hub_status(response)
                raise HeaderUnavailable(
                    "range_unsupported", "The download server did not honor a ranged read",
                )
            chunks = []
            received = 0
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                received += len(chunk)
                if received >= limit:
                    break
    except httpx.TimeoutException as exc:
        raise HeaderUnavailable("unreachable", "The model file download timed out") from exc
    except httpx.HTTPError as exc:
        raise HeaderUnavailable("unreachable", "The model file could not be reached") from exc
    return b"".join(chunks)[:limit]


async def _read_metadata(
    client: httpx.AsyncClient,
    location: str,
    expected_size: int | None,
) -> dict[str, Any]:
    data = b""
    want = FIRST_READ_BYTES
    while True:
        end = want - 1
        if expected_size:
            end = min(end, expected_size - 1)
        if end < len(data):
            raise HeaderUnavailable("unreadable", "The file ends before its GGUF metadata does")
        chunk = await _read_range(client, location, len(data), end)
        if not chunk:
            raise HeaderUnavailable("unreadable", "The model file returned no data")
        data += chunk
        try:
            parsed = parse_gguf_metadata(data)
        except GGUFTruncated:
            if len(data) >= MAX_READ_BYTES:
                raise HeaderUnavailable(
                    "header_too_large", "The GGUF metadata is larger than ODS reads before download",
                ) from None
            want = min(max(len(data) * 2, want), MAX_READ_BYTES)
            continue
        except NotGGUF as exc:
            raise HeaderUnavailable("not_gguf", "This file is not a GGUF model") from exc
        except (ValueError, UnicodeDecodeError, struct.error) as exc:
            raise HeaderUnavailable("unreadable", "The GGUF metadata could not be read") from exc
        parsed["bytes_read"] = len(data)
        return parsed


def cached_gguf_header(repo_id: str, revision: str, filename: str) -> dict[str, Any] | None:
    """A header this process already read, without any network request."""
    with _CACHE_LOCK:
        return _CACHE.get((repo_id, revision, filename))


async def fetch_gguf_header(
    repo_id: str,
    revision: str,
    filename: str,
    *,
    expected_size: int | None = None,
    token: str = "",
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Parsed GGUF metadata of one file at an immutable revision (cached)."""
    key = (repo_id, revision, filename)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=8.0),
            follow_redirects=False,
        )
    try:
        location = await _resolve(client, repo_id, revision, filename, expected_size, token)
        result = await _read_metadata(client, location, expected_size)
    finally:
        if owns_client:
            await client.aclose()
    with _CACHE_LOCK:
        _CACHE[key] = result
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX_ENTRIES:
            _CACHE.popitem(last=False)
    return result
