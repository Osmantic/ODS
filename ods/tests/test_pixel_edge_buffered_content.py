"""Regression test for un-terminated trailing buffered SSE in Pixel Edge."""

from __future__ import annotations

import asyncio as asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("PIXEL_OPENWEBUI_KEY", "test-pixel-openwebui-key-01234567890123456789")
os.environ.setdefault("PIXEL_PREVIEW_PROXY_KEY", "test-pixel-preview-proxy-key-01234567890123456789")

EDGE_DIR = Path(__file__).resolve().parents[1] / "extensions" / "services" / "pixel-edge"
edge = None


class _FakeStreamContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_any(self):
        for chunk in self._chunks:
            yield chunk


class _FakeUpstreamResponse:
    def __init__(self, chunks: list[bytes], status: int = 200) -> None:
        self.status = status
        self.content = _FakeStreamContent(chunks)


class _CaptureResponse:
    def __init__(self) -> None:
        self.status = 200
        self.written: list[bytes] = []

    async def prepare(self, _request) -> None:
        pass

    async def write(self, data: bytes) -> None:
        self.written.append(data)


class PixelEdgeBufferedContentTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        global edge
        try:
            import aiohttp  # noqa: F401
            sys.path.insert(0, str(EDGE_DIR))
            import pixel_edge as edge_module
            edge = edge_module
        except ImportError:
            raise unittest.SkipTest("aiohttp is required for pixel edge tests")
    def setUp(self) -> None:
        self.orig_stream_response = edge.web.StreamResponse

    def tearDown(self) -> None:
        edge.web.StreamResponse = self.orig_stream_response

    async def test_trailing_buffered_content_is_flushed_not_replaced(self) -> None:
        # Single chunk without trailing newline at EOF
        chunks = [b'data: {"model":"m","choices":[{"delta":{"content":"Tokyo"}}]}']
        resp = _FakeUpstreamResponse(chunks)
        capture = _CaptureResponse()
        edge.web.StreamResponse = lambda **kw: capture

        await edge._stream_upstream(MagicMock(), resp, "fallback reply text")
        output = b"".join(capture.written).decode("utf-8", errors="replace")

        self.assertIn("Tokyo", output)
        self.assertNotIn("fallback reply text", output)

    async def test_empty_trailing_buffer_synthesizes_finished_fallback(self) -> None:
        # Empty chunk without trailing newline at EOF triggers fallback with synthesized finish
        chunks = [b'data: {"model":"m","choices":[{"delta":{}}]}']
        resp = _FakeUpstreamResponse(chunks)
        capture = _CaptureResponse()
        edge.web.StreamResponse = lambda **kw: capture

        await edge._stream_upstream(MagicMock(), resp, "fallback reply text")
        output = b"".join(capture.written).decode("utf-8", errors="replace")

        self.assertIn("fallback reply text", output)
        self.assertIn('"finish_reason": "stop"', output)


if __name__ == "__main__":
    unittest.main()
