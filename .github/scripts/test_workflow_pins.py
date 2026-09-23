#!/usr/bin/env python3
"""Check that executable Action and container inputs retain immutable pins."""

import json
from pathlib import Path
import re
import unittest


GITHUB = Path(__file__).resolve().parents[1]


class WorkflowPins(unittest.TestCase):
    def test_root_action_refs_are_commit_pinned(self):
        for path in (GITHUB / "workflows").glob("*.yml"):
            for ref in re.findall(r"^\s*-?\s*uses:\s+([^\s#]+)", path.read_text(encoding="utf-8"), re.M):
                if ref.startswith("./"):
                    continue
                with self.subTest(workflow=path.name, action=ref):
                    self.assertRegex(ref, r"^[^@]+@[0-9a-f]{40}$")

    def test_ci_images_match_verified_manifest_ledger(self):
        ledger = json.loads((GITHUB / "ci-image-pins.json").read_text(encoding="utf-8"))
        expected = {item["source"] + "@" + item["digest"] for item in ledger["images"]}
        actual = set()
        for path in (GITHUB / "workflows").glob("*.yml"):
            for value in re.findall(r"^\s*(?:image|container):\s+([^\s#]+)", path.read_text(encoding="utf-8"), re.M):
                if value.startswith("${{"):
                    continue
                with self.subTest(workflow=path.name, image=value):
                    self.assertRegex(value, r"^[^@]+@sha256:[0-9a-f]{64}$")
                    self.assertIn(value, expected)
                    actual.add(value)
        self.assertEqual(actual, expected)
        for item in ledger["images"]:
            self.assertTrue(item["verified_manifest_sha256"])


if __name__ == "__main__":
    unittest.main()
