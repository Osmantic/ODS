"""Regression test for single-chunk and fast model generation in Pixel Edge SSE streaming."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

# Ensure required environment variables exist before importing pixel_edge
os.environ.setdefault("PIXEL_OPENWEBUI_KEY", "test-pixel-openwebui-key-01234567890123456789")
os.environ.setdefault("PIXEL_PREVIEW_PROXY_KEY", "test-pixel-preview-proxy-key-01234567890123456789")

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


class PixelEdgeSingleChunkStreamingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        global edge
        try:
            import aiohttp  # noqa: F401
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions" / "services" / "pixel-edge"))
            import pixel_edge as edge_module
            edge = edge_module
        except ImportError:
            raise unittest.SkipTest("aiohttp is required for pixel edge tests")
    async def _stream(self, chunks: list[bytes], fallback: str = "FALLBACK_CALLED") -> str:
        capture = _CaptureResponse()
        orig_stream_resp = edge.web.StreamResponse
        edge.web.StreamResponse = lambda **kwargs: capture  # type: ignore[assignment]
        try:
            await edge._stream_upstream(None, _FakeUpstreamResponse(chunks), fallback)
        finally:
            edge.web.StreamResponse = orig_stream_resp
        return b"".join(capture.written).decode("utf-8")

    async def test_single_chunk_with_finish_reason_delivers_content_without_fallback(self) -> None:
        chunks = [
            b"data: {\"choices\": [{\"delta\": {\"role\": \"assistant\"}}]}\n\n",
            b"data: {\"choices\": [{\"delta\": {\"content\": \"Paris\"}, \"finish_reason\": \"stop\"}]}\n\n",
            b"data: [DONE]\n\n",
        ]
        output = await self._stream(chunks)
        self.assertIn("Paris", output)
        self.assertNotIn("FALLBACK_CALLED", output)
        self.assertIn("data: [DONE]", output)

    async def test_single_chunk_with_reserved_reply_synthesizes_clean_fallback(self) -> None:
        chunks = [
            b"data: {\"choices\": [{\"delta\": {\"role\": \"assistant\"}}]}\n\n",
            b"data: {\"choices\": [{\"delta\": {\"content\": \"NO_REPLY\"}, \"finish_reason\": \"stop\"}]}\n\n",
            b"data: [DONE]\n\n",
        ]
        output = await self._stream(chunks)
        self.assertIn("FALLBACK_CALLED", output)
        self.assertNotIn("NO_REPLY", output)
        self.assertIn("data: [DONE]", output)

    async def test_single_chunk_with_empty_content_synthesizes_clean_fallback(self) -> None:
        chunks = [
            b"data: {\"choices\": [{\"delta\": {\"role\": \"assistant\"}}]}\n\n",
            b"data: {\"choices\": [{\"delta\": {}, \"finish_reason\": \"stop\"}]}\n\n",
            b"data: [DONE]\n\n",
        ]
        output = await self._stream(chunks)
        self.assertIn("FALLBACK_CALLED", output)
        self.assertIn("data: [DONE]", output)


if __name__ == "__main__":
    unittest.main()
