"""ODS Search Probe — distinguish upstream throttling from real agent failures.

Issue #1342: when SearXNG's upstream engines (DuckDuckGo, Brave) are
rate-limited or CAPTCHA'd, SearXNG returns empty results and the agent
honestly reports "all searches returned empty."  A naive probe that only
checks for a URL in the reply cannot distinguish this from a broken
web_search tool or a bridge that swallows results.

This module implements the decision matrix from issue #1342:

  agent reply        | direct SearXNG hits | verdict
  -------------------|---------------------|-----------------------------------
  URL present        | n/a                 | ok
  no URL, tool used, | 0                   | skip  (search_engines_throttled)
    claims empty     |                     |
  no URL, tool used  | >= 1                | fail  (agent_did_not_surface_results)
  no URL, tool NOT   | any                 | fail  (agent_did_not_invoke_web_search)
    used             |                     |
  SearXNG unreachable| n/a (no URL)        | skip  (searxng_unreachable)

Usage (production — direct SearXNG probe):
    from search_probe import classify_search_result, probe_searxng

    hits = await probe_searxng(searxng_url, query)
    verdict, reason = classify_search_result(
        agent_reply=reply_text,
        tool_was_called=tool_was_called,
        searxng_hit_count=hits,
    )

Usage (unit tests — pure classification, no network):
    verdict, reason = classify_search_result(
        agent_reply="I searched but found nothing.",
        tool_was_called=True,
        searxng_hit_count=0,   # mocked
    )
    assert verdict == "skip"
    assert reason  == "search_engines_throttled"
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Phrases an LLM typically uses when SearXNG returned no results.
_EMPTY_RESULT_PHRASES: tuple[str, ...] = (
    "empty results",
    "no results",
    "no search results",
    "searches returned empty",
    "returned empty",
    "couldn't find",
    "could not find",
    "nothing came up",
    "came up empty",
    "no relevant results",
    "no information found",
)

#: Simple URL pattern — presence of any URL signals a successful search pass-through.
_URL_RE = re.compile(r"https?://[^\s<>\"']{4,}", re.IGNORECASE)

# Sentinel for "SearXNG could not be reached at all."
SEARXNG_UNREACHABLE = -1

# ---------------------------------------------------------------------------
# Pure classification — no I/O
# ---------------------------------------------------------------------------


def classify_search_result(
    *,
    agent_reply: str,
    tool_was_called: bool,
    searxng_hit_count: int,
) -> tuple[str, str | None]:
    """Classify a search capability probe result.

    Args:
        agent_reply: The full text the agent returned to the user.
        tool_was_called: True when the Hermes bridge emitted a ``tool_start``
            event for ``web_search`` during this turn.
        searxng_hit_count: Number of results returned by a direct
            ``GET /search?format=json`` probe to SearXNG for the same query.
            Pass :data:`SEARXNG_UNREACHABLE` (-1) when SearXNG itself is
            unavailable.

    Returns:
        A ``(verdict, reason)`` tuple where:

        * ``verdict`` is one of ``"ok"``, ``"skip"``, or ``"fail"``.
        * ``reason`` is ``None`` on ``"ok"``, otherwise a short snake_case
          string explaining the skip or failure.
    """
    has_url = bool(_URL_RE.search(agent_reply))
    claims_empty = any(
        phrase in agent_reply.lower() for phrase in _EMPTY_RESULT_PHRASES
    )

    # Happy path — agent surfaced at least one URL from the search results.
    if has_url:
        return "ok", None

    # Agent never called the tool — real bug regardless of SearXNG state.
    if not tool_was_called:
        return "fail", "agent_did_not_invoke_web_search"

    # Tool was called but agent has no URL to show.

    if searxng_hit_count == SEARXNG_UNREACHABLE:
        # SearXNG itself is down — we can't blame the agent for empty results.
        return "skip", "searxng_unreachable"

    if claims_empty and searxng_hit_count == 0:
        # Agent honestly reported empty results and SearXNG confirms engines are
        # returning nothing — upstream throttling, not an ODS bug.
        return "skip", "search_engines_throttled"

    if searxng_hit_count >= 1:
        # SearXNG found results but the agent did not surface them — real bug:
        # the web_search bridge is swallowing results.
        return "fail", "agent_did_not_surface_results"

    # Tool called, no URL, no empty-result claim, SearXNG returned 0 hits.
    # Treat as throttled — the agent may have phrased its empty reply differently.
    if searxng_hit_count == 0:
        return "skip", "search_engines_throttled"

    # Shouldn't reach here, but be safe.
    return "skip", "search_engines_throttled"


# ---------------------------------------------------------------------------
# Async SearXNG direct probe — used by fleet / integration callers
# ---------------------------------------------------------------------------


async def probe_searxng(searxng_url: str, query: str, timeout: float = 8.0) -> int:
    """Hit SearXNG directly and return the number of results for *query*.

    Returns :data:`SEARXNG_UNREACHABLE` if SearXNG cannot be reached or
    returns a non-JSON / error response.

    Args:
        searxng_url: Base URL of the SearXNG service, e.g.
            ``"http://127.0.0.1:8888"`` (host-mapped port, not the container
            alias ``http://searxng:8080`` which is only resolvable inside the
            Docker network).
        query: The search query string.
        timeout: HTTP request timeout in seconds.
    """
    try:
        import httpx  # local import so the module is importable without httpx in tests
    except ImportError:
        return SEARXNG_UNREACHABLE

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{searxng_url.rstrip('/')}/search",
                params={"q": query, "format": "json"},
            )
            if resp.status_code >= 400:
                return SEARXNG_UNREACHABLE
            data = resp.json()
            return len(data.get("results", []))
    except Exception:  # noqa: BLE001 — any network/parse failure → unreachable
        return SEARXNG_UNREACHABLE
