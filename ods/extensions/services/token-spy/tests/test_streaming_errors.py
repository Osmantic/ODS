"""Streaming upstream error privacy regressions."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import pytest
from pathlib import Path
from uuid import uuid4


TOKEN_SPY_DIR = Path(__file__).resolve().parent.parent

PRIVATE_PROMPT_SENTINEL = "PRIVATE_PROMPT_SENTINEL"


@pytest.fixture
def token_spy_main(monkeypatch):
    """Load main.py under a unique module name.

    Three services in this repo ship a `main.py`, so a plain `import main` is
    ambiguous once pytest runs from anywhere but this directory. Mirrors the
    isolation `test_usage_report.py` uses for `db.py`.
    """
    # main.py generates and writes an API key at import when this is unset.
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "test-token-spy-key")
    monkeypatch.syspath_prepend(str(TOKEN_SPY_DIR))

    spec = importlib.util.spec_from_file_location(
        f"token_spy_main_{uuid4().hex}",
        TOKEN_SPY_DIR / "main.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _MockUpstream:
    """Minimal stand-in for an httpx streaming response that errored."""

    def __init__(self, body: bytes, status_code: int = 400):
        self._body = body
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_bytes(self):
        # Split so the handler is exercised on a multi-chunk error body.
        midpoint = len(self._body) // 2
        yield self._body[:midpoint]
        yield self._body[midpoint:]


class _MockClient:
    def __init__(self, body: bytes, status_code: int = 400):
        self._body = body
        self._status_code = status_code

    def stream(self, *args, **kwargs):
        return _MockUpstream(self._body, self._status_code)


def _consume(module, client) -> str:
    async def run():
        response = await module._handle_openai_streaming(
            client,
            b'{"messages":[]}',
            {},
            "test-model",
            {},
            {},
            [],
            0,
        )
        return "".join([chunk async for chunk in response.body_iterator])

    return asyncio.run(run())


def test_openai_streaming_error_preserves_body_without_logging_content(
    token_spy_main, caplog
):
    """Upstream error bodies reach the client but never the log.

    Provider errors quote the request that failed, and `ods-support-bundle.sh`
    collects container logs by default while only redacting secret-shaped
    values -- so prompt text logged here would ship in a bundle attached to a
    public issue.
    """
    error_body = (
        '{"error":{"type":"invalid_request_error","code":"context_length_exceeded",'
        f'"message":"invalid input: {PRIVATE_PROMPT_SENTINEL}"'
        "}}"
    ).encode()

    with caplog.at_level(logging.ERROR, logger="token-monitor"):
        returned_body = _consume(token_spy_main, _MockClient(error_body))

    assert returned_body == f"data: {error_body.decode()}\n\n"
    assert PRIVATE_PROMPT_SENTINEL not in caplog.text
    assert "invalid input" not in caplog.text
    assert "type=invalid_request_error" in caplog.text
    assert "code=context_length_exceeded" in caplog.text
    assert f"{len(error_body)} byte body withheld from logs" in caplog.text


def test_describe_upstream_error_reports_identifiers(token_spy_main):
    summary = token_spy_main._describe_upstream_error(
        429,
        b'{"error":{"type":"rate_limit_error","code":"slow_down","message":"secret"}}',
    )

    assert "Upstream 429" in summary
    assert "type=rate_limit_error code=slow_down" in summary
    assert "secret" not in summary


@pytest.mark.parametrize(
    "body",
    [
        b"<html>502 Bad Gateway PRIVATE_PROMPT_SENTINEL</html>",  # not JSON
        b'["PRIVATE_PROMPT_SENTINEL"]',  # JSON, but not an object
        b'{"error":"PRIVATE_PROMPT_SENTINEL"}',  # error is a string, not an object
        b'{"error":{"message":"PRIVATE_PROMPT_SENTINEL"}}',  # no type/code
        b"\xff\xfe PRIVATE_PROMPT_SENTINEL",  # undecodable bytes
    ],
)
def test_describe_upstream_error_never_echoes_body(token_spy_main, body):
    summary = token_spy_main._describe_upstream_error(500, body)

    assert PRIVATE_PROMPT_SENTINEL not in summary
    assert f"{len(body)} byte body withheld from logs" in summary


def test_describe_upstream_error_truncates_untrusted_identifiers(token_spy_main):
    """type/code come from upstream, so they are capped rather than trusted."""
    long_code = "x" * 500
    summary = token_spy_main._describe_upstream_error(
        400, f'{{"error":{{"code":"{long_code}"}}}}'.encode()
    )

    assert "x" * 64 in summary
    assert "x" * 65 not in summary
