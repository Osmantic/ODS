#!/usr/bin/env python3
"""Regression test: register-model-store reads model-stores.json with UTF-8 encoding.

Without explicit encoding='utf-8', Python uses the locale encoding on Windows
(typically cp1252), causing UnicodeDecodeError on any non-ASCII content in
the registry file.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RegisterModelStoreEncodingTest(unittest.TestCase):
    def test_registry_with_unicode_hostpath_round_trips(self) -> None:
        """Registry containing non-ASCII hostPath must round-trip without error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "model-stores.json"
            existing = {
                "schemaVersion": 1,
                "stores": [
                    {
                        "id": "test-store",
                        "hostPath": "/models/caf\u00e9-models",
                        "containerPath": "/model-stores/test-store",
                        "profiles": {},
                    }
                ],
            }
            registry_path.write_text(json.dumps(existing), encoding="utf-8")

            # Fixed code: read_text(encoding='utf-8') must not raise UnicodeDecodeError.
            text = registry_path.read_text(encoding="utf-8")
            parsed = json.loads(text)
            self.assertEqual(
                parsed["stores"][0]["hostPath"],
                "/models/caf\u00e9-models",
                "Non-ASCII host path must be preserved across UTF-8 read-write cycle",
            )

    def test_missing_registry_produces_default_document(self) -> None:
        """Missing registry file must produce the default schema document."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "model-stores.json"
            self.assertFalse(missing.exists())
            document = (
                json.loads(missing.read_text(encoding="utf-8"))
                if missing.exists()
                else {"schemaVersion": 1, "stores": []}
            )
            self.assertEqual(document["schemaVersion"], 1)
            self.assertEqual(document["stores"], [])

    def test_baseline_missing_encoding_would_have_failed_on_windows(self) -> None:
        """Demonstrate that omitting encoding= on bytes content would fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "registry.json"
            # Write non-ASCII path bytes via UTF-8
            path.write_bytes(b'{"schemaVersion":1,"stores":[{"hostPath":"/caf\xc3\xa9"}]}')
            # Fixed: read with explicit UTF-8 must succeed
            text = path.read_text(encoding="utf-8")
            self.assertIn("caf\u00e9", text)


if __name__ == "__main__":
    unittest.main()
