"""Regression test for Pixel chat stream reasoning tokens and tool calls."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("DASHBOARD_API_KEY", "test-key-123456789012345678901234")
os.environ.setdefault("ODS_DATA_DIR", "/tmp")

API_DIR = Path(__file__).resolve().parents[1] / "extensions" / "services" / "dashboard-api"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(API_DIR / "tests"))

receipts = None
pixel = None
pixel_chat_identity = None
ConnectedRequest = None
FakeClient = None
FakeResponse = None


class PixelChatReasoningStreamTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        global receipts, pixel, pixel_chat_identity, ConnectedRequest, FakeClient, FakeResponse
        try:
            import fastapi  # noqa: F401
            import httpx  # noqa: F401
            import pixel_chat_identity as pci
            import pixel_chat_results as r
            from routers import pixel as px
            from test_pixel import ConnectedRequest as cr, FakeClient as fc, FakeResponse as fr

            pixel_chat_identity = pci
            receipts = r
            pixel = px
            ConnectedRequest = cr
            FakeClient = fc
            FakeResponse = fr
        except ImportError:
            raise unittest.SkipTest("fastapi and httpx are required for pixel chat stream tests")
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.receipts_path = Path(self.temp_dir.name).resolve() / "receipts"
        self.store = receipts.ChatResultStore(self.receipts_path)
        pixel._result_store = self.store
        pixel._result_tasks = {}
        pixel._result_stops = set()
        pixel._result_abort_ack = set()
        os.environ["PIXEL_OPENWEBUI_KEY"] = "e" * 64

        async def ready():
            return None

        async def identity(*_a, **_kw):
            return {"schemaVersion": 1, "revision": 1, "displayName": "Portal"}

        pixel._model_readiness_issue = ready
        self.orig_identity = pixel_chat_identity.async_request_json
        self.orig_client = pixel.httpx.AsyncClient
        pixel_chat_identity.async_request_json = identity

    async def asyncTearDown(self) -> None:
        pixel.httpx.AsyncClient = self.orig_client
        pixel_chat_identity.async_request_json = self.orig_identity
        self.store.close()
        self.temp_dir.cleanup()

    async def test_stream_with_reasoning_content_is_marked_complete(self) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"reasoning_content":"model thinking step"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        pixel.httpx.AsyncClient = lambda **kw: FakeClient(
            FakeResponse(content_type="text/event-stream", chunks=chunks)
        )
        body = pixel.ChatStreamRequest(
            chat_id="chat-reasoning",
            request_id="req-1",
            messages=[{"role": "user", "content": "hello"}],
        )
        await pixel.pixel_chat_stream(ConnectedRequest(), body, "owner1")
        await asyncio.gather(*list(pixel._result_tasks.values()))
        result = await pixel.pixel_chat_result(
            pixel.ChatResultRequest(chat_id="chat-reasoning", request_id="req-1"), "owner1"
        )
        self.assertEqual(result["state"], "complete")
        self.assertNotIn("Pixel returned no answer", result["events"])

    async def test_stream_with_tool_calls_is_marked_complete(self) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"tool_calls":[{"id":"call_1","type":"function"}]}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        pixel.httpx.AsyncClient = lambda **kw: FakeClient(
            FakeResponse(content_type="text/event-stream", chunks=chunks)
        )
        body = pixel.ChatStreamRequest(
            chat_id="chat-tool",
            request_id="req-2",
            messages=[{"role": "user", "content": "query"}],
        )
        await pixel.pixel_chat_stream(ConnectedRequest(), body, "owner1")
        await asyncio.gather(*list(pixel._result_tasks.values()))
        result = await pixel.pixel_chat_result(
            pixel.ChatResultRequest(chat_id="chat-tool", request_id="req-2"), "owner1"
        )
        self.assertEqual(result["state"], "complete")
        self.assertNotIn("Pixel returned no answer", result["events"])

    async def test_stream_without_tokens_remains_interrupted(self) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        pixel.httpx.AsyncClient = lambda **kw: FakeClient(
            FakeResponse(content_type="text/event-stream", chunks=chunks)
        )
        body = pixel.ChatStreamRequest(
            chat_id="chat-empty",
            request_id="req-3",
            messages=[{"role": "user", "content": "empty"}],
        )
        await pixel.pixel_chat_stream(ConnectedRequest(), body, "owner1")
        await asyncio.gather(*list(pixel._result_tasks.values()))
        result = await pixel.pixel_chat_result(
            pixel.ChatResultRequest(chat_id="chat-empty", request_id="req-3"), "owner1"
        )
        self.assertEqual(result["state"], "interrupted")
        self.assertIn("Pixel returned no answer", result["events"])


if __name__ == "__main__":
    unittest.main()
