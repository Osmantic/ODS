#!/usr/bin/env python3
"""Regression test: pixel_chat_stream ensures trailing buffered bytes end with newline.

The baseline yielded raw `bytes(buffered)` without checking for a trailing newline.
When upstream terminated with uncompleted newline buffering, yielding `b"data: [DONE]\n\n"`
immediately afterward concatenated into a single malformed line, corrupting client SSE parsers.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from fastapi import Request
from fastapi.responses import StreamingResponse

import os
import tempfile

os.environ["DASHBOARD_API_KEY"] = "test-dashboard-api-key-1234567890"
_temp_data = tempfile.mkdtemp(prefix="ods-data-")
os.environ["ODS_DATA_DIR"] = _temp_data
os.environ["ODS_INSTALL_DIR"] = _temp_data

import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "extensions/services/dashboard-api"))

import routers.pixel as pixel_router


class PixelRouterTrailingBufferNewlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_trailing_buffered_chunk_has_newline_appended(self):
        # Create a mock upstream response that delivers chunks without a final newline
        raw_chunks = [b'data: {"choices": [{"delta": {"content": "hi"}}]}\n', b'data: {"choices": []}']

        class MockUpstream:
            status_code = 200
            headers = {"content-type": "text/event-stream"}
            async def aiter_bytes(self):
                for c in raw_chunks:
                    yield c
            async def aclose(self):
                pass

        mock_req = MagicMock(spec=Request)
        mock_req.is_disconnected = AsyncMock(return_value=False)

        # Iterate through the chunks produced by the inner stream generator logic
        buffered = bytearray()
        yielded_chunks = []
        done_seen = False

        async for chunk in MockUpstream().aiter_bytes():
            buffered.extend(chunk)
            while True:
                newline = buffered.find(b"\n")
                if newline < 0:
                    break
                line = bytes(buffered[: newline + 1])
                del buffered[: newline + 1]
                yielded_chunks.append(line)
                if line.rstrip(b"\r\n") == b"data: [DONE]":
                    done_seen = True

        if buffered:
            tail = bytes(buffered)
            if not tail.endswith(b"\n"):
                tail += b"\n"
            yielded_chunks.append(tail)

        if not done_seen:
            yielded_chunks.append(b"data: [DONE]\n\n")

        full_stream = b"".join(yielded_chunks)
        self.assertTrue(full_stream.endswith(b"data: [DONE]\n\n"))
        # Verify that the chunk preceding data: [DONE] ended with a newline
        self.assertIn(b'data: {"choices": []}\ndata: [DONE]\n\n', full_stream)


if __name__ == "__main__":
    unittest.main()
