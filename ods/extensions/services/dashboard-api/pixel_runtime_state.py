"""Process-local coordination between Pixel streams and model activation."""

from __future__ import annotations

import os
import threading

import httpx


_lock = threading.Lock()
_active_streams = 0
_PIXEL_EDGE_ACTIVITY_URL = "http://pixel-edge:9595/v1/activity"
_MAX_ACTIVITY_BYTES = 1024


def begin_pixel_stream() -> None:
    """Record a dashboard Pixel stream after its upstream accepts the turn."""
    global _active_streams
    with _lock:
        _active_streams += 1


def end_pixel_stream() -> None:
    """Release one dashboard Pixel stream without allowing counter underflow."""
    global _active_streams
    with _lock:
        _active_streams = max(0, _active_streams - 1)


def _local_pixel_stream_active() -> bool:
    with _lock:
        return _active_streams > 0


def _pixel_edge_stream_active() -> bool:
    """Read the content-free activity projection shared with Open WebUI."""
    key = os.environ.get("PIXEL_OPENWEBUI_KEY", "")
    if not key or key != key.strip() or len(key) < 32 or len(key) > 4096:
        return False
    try:
        timeout = httpx.Timeout(connect=2.0, read=2.0, write=2.0, pool=2.0)
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            response = client.get(
                _PIXEL_EDGE_ACTIVITY_URL,
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            )
        if response.status_code != 200:
            return False
        if not response.headers.get("content-type", "").lower().startswith("application/json"):
            return False
        if len(response.content) > _MAX_ACTIVITY_BYTES:
            return False
        payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return False
    return (
        isinstance(payload, dict)
        and set(payload) == {"active", "streams"}
        and payload.get("active") is True
        and isinstance(payload.get("streams"), int)
        and not isinstance(payload.get("streams"), bool)
        and payload["streams"] > 0
    )


def pixel_stream_active() -> bool:
    """Return whether model activation would interrupt any live Pixel turn."""
    return _local_pixel_stream_active() or _pixel_edge_stream_active()
