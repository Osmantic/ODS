"""The dashboard's CSRF rule, shared by dashboard-api and the native chat origin.

CORS governs whether a page may *read* a response, not whether the request
runs, so state-changing requests are checked here: a browser request whose
Origin does not name the same host:port as Host, or that the browser marks
``Sec-Fetch-Site: cross-site``, is rejected. Requests without Origin (ods-cli,
the host agent, other non-browser callers) are left alone. See main.py for the
full rationale.
"""

from __future__ import annotations

from urllib.parse import urlparse

STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def origin_matches_host(origin: str, host_header: str | None) -> bool:
    """True when Origin names the same host:port as the Host header."""
    if not host_header:
        return False
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    if not parsed.hostname:
        return False
    return parsed.netloc == host_header


def is_cross_site_state_change(method: str, origin: str | None, host: str | None,
                               sec_fetch_site: str | None) -> bool:
    if method not in STATE_CHANGING_METHODS:
        return False
    # Sec-Fetch-Site is set by the browser and cannot be forged by page script.
    cross_site = sec_fetch_site == "cross-site"
    if origin is None and not cross_site:
        return False
    return cross_site or not origin_matches_host(origin, host)
