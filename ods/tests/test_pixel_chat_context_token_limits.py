#!/usr/bin/env python3
"""Regression test: pixel_chat_context coerces integer float token measurements.

The baseline configured _Projection with strict=True, causing Pydantic to reject
exact integer floats (such as 100.0 or 2048.0) commonly produced by JSON deserializers
or arithmetic token counting operations with ValidationError.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "extensions/services/dashboard-api"))

from pixel_chat_context import public_context


class PixelChatContextTokenLimitsTests(unittest.TestCase):
    def setUp(self):
        self.base_context = {
            "schemaVersion": 1,
            "status": "ready",
            "sessionRevision": "rev-1",
            "context": {
                "used": 100,
                "window": 2048,
                "measuredAt": "2026-09-19T10:00:00Z",
            },
            "model": {
                "id": "model-1",
                "provider": "prov-1",
                "contextWindow": 2048,
            },
            "compaction": {"status": "idle", "count": 0},
            "history": {
                "status": "ready",
                "revision": "a" * 64,
                "acknowledgedMessages": 5,
            },
        }

    def test_exact_integer_floats_coerced(self):
        # Pass float values for used, window, and contextWindow
        data = dict(self.base_context)
        data["context"] = dict(data["context"], used=100.0, window=2048.0)
        data["model"] = dict(data["model"], contextWindow=2048.0)

        result = public_context(data)
        self.assertEqual(result["context"]["used"], 100)
        self.assertIsInstance(result["context"]["used"], int)
        self.assertEqual(result["context"]["window"], 2048)
        self.assertIsInstance(result["context"]["window"], int)
        self.assertEqual(result["model"]["contextWindow"], 2048)
        self.assertIsInstance(result["model"]["contextWindow"], int)

    def test_non_integer_floats_still_rejected(self):
        data = dict(self.base_context)
        data["context"] = dict(data["context"], used=100.5)
        with self.assertRaises(Exception):
            public_context(data)


if __name__ == "__main__":
    unittest.main()
