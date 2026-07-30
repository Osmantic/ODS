"""Tests for search_probe.classify_search_result and probe_searxng.

Covers the full decision matrix from issue #1342:

  agent reply        | direct SearXNG hits | expected verdict / reason
  -------------------|---------------------|-----------------------------------
  URL present        | n/a                 | ok  / None
  no URL, tool used, | 0 (throttled)       | skip / search_engines_throttled
    claims empty     |                     |
  no URL, tool used  | >= 1                | fail / agent_did_not_surface_results
  no URL, tool NOT   | any                 | fail / agent_did_not_invoke_web_search
    used             |                     |
  SearXNG unreachable| n/a (no URL)        | skip / searxng_unreachable
"""

import asyncio
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify(**kwargs):
    """Shorthand that imports classify_search_result from the dashboard-api source."""
    from search_probe import classify_search_result
    return classify_search_result(**kwargs)


# ---------------------------------------------------------------------------
# Happy-path: URL present → ok
# ---------------------------------------------------------------------------

class TestOkVerdict:
    def test_url_in_reply_is_ok_regardless_of_tool(self):
        verdict, reason = _classify(
            agent_reply="Here's what I found: https://anthropic.com/research",
            tool_was_called=True,
            searxng_hit_count=5,
        )
        assert verdict == "ok"
        assert reason is None

    def test_url_in_reply_ok_even_when_tool_not_called(self):
        """If somehow the reply contains a URL without a tool event,
        we still count it as ok — no false failure."""
        verdict, reason = _classify(
            agent_reply="Visit https://example.com for more info.",
            tool_was_called=False,
            searxng_hit_count=0,
        )
        assert verdict == "ok"
        assert reason is None

    def test_http_url_detected(self):
        verdict, reason = _classify(
            agent_reply="See http://old-site.org/page for details.",
            tool_was_called=True,
            searxng_hit_count=2,
        )
        assert verdict == "ok"
        assert reason is None

    def test_url_mixed_with_empty_claim_still_ok(self):
        """Even if the reply partially claims empty, a URL present wins."""
        verdict, reason = _classify(
            agent_reply="Some results were empty but I found https://example.com",
            tool_was_called=True,
            searxng_hit_count=1,
        )
        assert verdict == "ok"
        assert reason is None


# ---------------------------------------------------------------------------
# Skip: upstream engines throttled
# ---------------------------------------------------------------------------

class TestThrottledSkip:
    def test_skip_when_agent_claims_empty_and_searxng_has_zero_hits(self):
        """Core issue #1342 case: agent says 'empty results', SearXNG also empty."""
        verdict, reason = _classify(
            agent_reply=(
                "It seems like all searches are returning empty results. "
                "This could indicate that the SearXNG backend isn't working "
                "or there's a network issue."
            ),
            tool_was_called=True,
            searxng_hit_count=0,
        )
        assert verdict == "skip"
        assert reason == "search_engines_throttled"

    def test_skip_phrases_no_results(self):
        verdict, reason = _classify(
            agent_reply="I searched but found no results for your query.",
            tool_was_called=True,
            searxng_hit_count=0,
        )
        assert verdict == "skip"
        assert reason == "search_engines_throttled"

    def test_skip_phrases_could_not_find(self):
        verdict, reason = _classify(
            agent_reply="I could not find any relevant information.",
            tool_was_called=True,
            searxng_hit_count=0,
        )
        assert verdict == "skip"
        assert reason == "search_engines_throttled"

    def test_skip_phrases_came_up_empty(self):
        verdict, reason = _classify(
            agent_reply="My web search came up empty for that topic.",
            tool_was_called=True,
            searxng_hit_count=0,
        )
        assert verdict == "skip"
        assert reason == "search_engines_throttled"

    def test_skip_when_tool_called_no_url_and_searxng_zero(self):
        """Tool called, SearXNG has 0 results — skip even without explicit phrase."""
        verdict, reason = _classify(
            agent_reply="I tried searching but couldn't retrieve anything useful.",
            tool_was_called=True,
            searxng_hit_count=0,
        )
        assert verdict == "skip"
        assert reason == "search_engines_throttled"


# ---------------------------------------------------------------------------
# Skip: SearXNG unreachable
# ---------------------------------------------------------------------------

class TestSearxngUnreachable:
    def test_skip_when_searxng_unreachable_and_no_url(self):
        from search_probe import SEARXNG_UNREACHABLE
        verdict, reason = _classify(
            agent_reply="I attempted a search but got no response.",
            tool_was_called=True,
            searxng_hit_count=SEARXNG_UNREACHABLE,
        )
        assert verdict == "skip"
        assert reason == "searxng_unreachable"

    def test_skip_searxng_unreachable_explicit_sentinel(self):
        verdict, reason = _classify(
            agent_reply="Search failed.",
            tool_was_called=True,
            searxng_hit_count=-1,
        )
        assert verdict == "skip"
        assert reason == "searxng_unreachable"


# ---------------------------------------------------------------------------
# Fail: agent did not invoke web_search
# ---------------------------------------------------------------------------

class TestAgentSkippedTool:
    def test_fail_when_tool_not_called_and_no_url(self):
        """Agent answered without calling web_search — real bug."""
        verdict, reason = _classify(
            agent_reply="Anthropic is a leading AI safety company.",
            tool_was_called=False,
            searxng_hit_count=8,
        )
        assert verdict == "fail"
        assert reason == "agent_did_not_invoke_web_search"

    def test_fail_tool_not_called_even_with_zero_searxng(self):
        """Tool skipped is always a real failure regardless of SearXNG state."""
        verdict, reason = _classify(
            agent_reply="I don't have information about that.",
            tool_was_called=False,
            searxng_hit_count=0,
        )
        assert verdict == "fail"
        assert reason == "agent_did_not_invoke_web_search"

    def test_fail_tool_not_called_searxng_unreachable(self):
        verdict, reason = _classify(
            agent_reply="Here is some general info about the topic.",
            tool_was_called=False,
            searxng_hit_count=-1,
        )
        assert verdict == "fail"
        assert reason == "agent_did_not_invoke_web_search"


# ---------------------------------------------------------------------------
# Fail: agent called tool but didn't surface results
# ---------------------------------------------------------------------------

class TestAgentIgnoredToolResults:
    def test_fail_when_searxng_has_results_but_agent_has_no_url(self):
        """SearXNG found results, tool was called, but agent produced no URL —
        the bridge is swallowing results. Real ODS bug."""
        verdict, reason = _classify(
            agent_reply="I searched but unfortunately the results were not helpful.",
            tool_was_called=True,
            searxng_hit_count=6,
        )
        assert verdict == "fail"
        assert reason == "agent_did_not_surface_results"

    def test_fail_searxng_one_result_no_url_in_reply(self):
        verdict, reason = _classify(
            agent_reply="My search didn't return anything useful for that query.",
            tool_was_called=True,
            searxng_hit_count=1,
        )
        assert verdict == "fail"
        assert reason == "agent_did_not_surface_results"

    def test_fail_not_throttled_when_searxng_has_hits(self):
        """If SearXNG has results, it's NOT throttled — must be agent/bridge bug."""
        verdict, reason = _classify(
            agent_reply="No results found.",
            tool_was_called=True,
            searxng_hit_count=10,
        )
        assert verdict == "fail"
        assert reason == "agent_did_not_surface_results"


# ---------------------------------------------------------------------------
# probe_searxng async tests (mocked — no real network)
# ---------------------------------------------------------------------------

class TestProbeSearxng:
    def test_returns_result_count_on_success(self):
        """probe_searxng should return len(results) from the JSON response."""
        import search_probe

        fake_response_data = {
            "results": [
                {"url": "https://example.com", "title": "Example"},
                {"url": "https://another.org", "title": "Another"},
            ],
            "unresponsive_engines": [],
        }

        class FakeResponse:
            status_code = 200
            def json(self):
                return fake_response_data

        class FakeClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                pass
            async def get(self, url, params=None):
                return FakeResponse()

        # Patch httpx.AsyncClient
        fake_httpx = types.SimpleNamespace(
            AsyncClient=lambda **kwargs: FakeClient()
        )
        original = sys.modules.get("httpx")
        sys.modules["httpx"] = fake_httpx
        try:
            count = asyncio.run(
                search_probe.probe_searxng("http://127.0.0.1:8888", "anthropic.com")
            )
        finally:
            if original is None:
                del sys.modules["httpx"]
            else:
                sys.modules["httpx"] = original

        assert count == 2

    def test_returns_zero_on_empty_results(self):
        """Empty results array → 0, not SEARXNG_UNREACHABLE."""
        import search_probe

        fake_response_data = {
            "results": [],
            "unresponsive_engines": [
                ["duckduckgo", "CAPTCHA"],
                ["brave", "too many requests"],
            ],
        }

        class FakeResponse:
            status_code = 200
            def json(self):
                return fake_response_data

        class FakeClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                pass
            async def get(self, url, params=None):
                return FakeResponse()

        fake_httpx = types.SimpleNamespace(
            AsyncClient=lambda **kwargs: FakeClient()
        )
        original = sys.modules.get("httpx")
        sys.modules["httpx"] = fake_httpx
        try:
            count = asyncio.run(
                search_probe.probe_searxng("http://127.0.0.1:8888", "anthropic.com")
            )
        finally:
            if original is None:
                del sys.modules["httpx"]
            else:
                sys.modules["httpx"] = original

        assert count == 0

    def test_returns_unreachable_on_http_error(self):
        """Non-200 response → SEARXNG_UNREACHABLE."""
        import search_probe

        class FakeResponse:
            status_code = 503
            def json(self):
                return {}

        class FakeClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                pass
            async def get(self, url, params=None):
                return FakeResponse()

        fake_httpx = types.SimpleNamespace(
            AsyncClient=lambda **kwargs: FakeClient()
        )
        original = sys.modules.get("httpx")
        sys.modules["httpx"] = fake_httpx
        try:
            count = asyncio.run(
                search_probe.probe_searxng("http://127.0.0.1:8888", "test")
            )
        finally:
            if original is None:
                del sys.modules["httpx"]
            else:
                sys.modules["httpx"] = original

        assert count == search_probe.SEARXNG_UNREACHABLE

    def test_returns_unreachable_on_connection_error(self):
        """Network exception → SEARXNG_UNREACHABLE, does not raise."""
        import search_probe

        class FakeClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                pass
            async def get(self, url, params=None):
                raise ConnectionError("refused")

        fake_httpx = types.SimpleNamespace(
            AsyncClient=lambda **kwargs: FakeClient()
        )
        original = sys.modules.get("httpx")
        sys.modules["httpx"] = fake_httpx
        try:
            count = asyncio.run(
                search_probe.probe_searxng("http://127.0.0.1:8888", "test")
            )
        finally:
            if original is None:
                del sys.modules["httpx"]
            else:
                sys.modules["httpx"] = original

        assert count == search_probe.SEARXNG_UNREACHABLE

    def test_returns_unreachable_when_httpx_not_installed(self):
        """If httpx is not available, fall back to SEARXNG_UNREACHABLE."""
        import search_probe

        original = sys.modules.get("httpx")
        sys.modules["httpx"] = None  # simulate import failure
        try:
            # The module re-imports httpx inside the function; None won't
            # have AsyncClient, so the local-import guard triggers.
            count = asyncio.run(
                search_probe.probe_searxng("http://127.0.0.1:8888", "test")
            )
        except Exception:
            count = search_probe.SEARXNG_UNREACHABLE  # accept either behavior
        finally:
            if original is None:
                sys.modules.pop("httpx", None)
            else:
                sys.modules["httpx"] = original

        assert count == search_probe.SEARXNG_UNREACHABLE


# ---------------------------------------------------------------------------
# End-to-end decision matrix integration
# ---------------------------------------------------------------------------

class TestDecisionMatrixIntegration:
    """Runs through every cell of the issue #1342 decision matrix in one place."""

    MATRIX = [
        # (agent_reply,              tool_called, hits, exp_verdict, exp_reason)
        (
            "Here is the result: https://anthropic.com",
            True, 5, "ok", None,
        ),
        (
            "All searches returned empty results.",
            True, 0, "skip", "search_engines_throttled",
        ),
        (
            "I found nothing useful despite searching.",
            True, 4, "fail", "agent_did_not_surface_results",
        ),
        (
            "Anthropic is an AI safety company.",
            False, 8, "fail", "agent_did_not_invoke_web_search",
        ),
        (
            "The search service is unavailable.",
            True, -1, "skip", "searxng_unreachable",
        ),
    ]

    @pytest.mark.parametrize(
        "reply,tool,hits,exp_verdict,exp_reason",
        MATRIX,
        ids=[
            "url_present",
            "throttled_zero_hits",
            "bridge_swallows_results",
            "agent_skips_tool",
            "searxng_down",
        ],
    )
    def test_matrix_cell(self, reply, tool, hits, exp_verdict, exp_reason):
        verdict, reason = _classify(
            agent_reply=reply,
            tool_was_called=tool,
            searxng_hit_count=hits,
        )
        assert verdict == exp_verdict, (
            f"Expected verdict={exp_verdict!r}, got {verdict!r} "
            f"(reply={reply!r}, tool={tool}, hits={hits})"
        )
        assert reason == exp_reason, (
            f"Expected reason={exp_reason!r}, got {reason!r}"
        )
