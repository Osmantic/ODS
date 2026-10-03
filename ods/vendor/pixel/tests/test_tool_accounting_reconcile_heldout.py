from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.test_tool_accounting_reconcile import ROOT, SCRIPT, event, run_case, transcript


def run_paths(
    declared: Path,
    observed: Path,
    output: Path,
    *,
    declared_format: str = "accounting",
    transcript_format: str = "events",
    event_type: str = "tool-call",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--declared",
            str(declared),
            "--declared-format",
            declared_format,
            "--transcript",
            str(observed),
            "--transcript-format",
            transcript_format,
            "--event-type",
            event_type,
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )


class ToolAccountingHeldoutTests(unittest.TestCase):
    def test_directory_inputs_fail_without_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            valid = directory / "valid.json"
            valid.write_bytes(b"0")
            for declared, observed in ((directory, valid), (valid, directory)):
                output = directory / f"receipt-{len(list(directory.iterdir()))}.json"
                result = run_paths(declared, observed, output)
                self.assertEqual(2, result.returncode)
                self.assertFalse(output.exists())

    @unittest.skipIf(os.name == "nt", "POSIX symlink custody")
    def test_input_and_output_symlinks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            declared = directory / "declared.json"
            observed = directory / "transcript.jsonl"
            declared.write_bytes(b"0")
            observed.write_bytes(b"")

            declared_link = directory / "declared-link.json"
            declared_link.symlink_to(declared)
            result = run_paths(declared_link, observed, directory / "input-receipt.json")
            self.assertEqual(2, result.returncode)
            self.assertFalse((directory / "input-receipt.json").exists())

            owner_file = directory / "owner-file.json"
            owner_file.write_bytes(b"owner-bytes-must-survive")
            output_link = directory / "receipt-link.json"
            output_link.symlink_to(owner_file)
            result = run_paths(declared, observed, output_link)
            self.assertEqual(2, result.returncode)
            self.assertTrue(output_link.is_symlink())
            self.assertEqual(b"owner-bytes-must-survive", owner_file.read_bytes())

    def test_declared_byte_ceiling_fails_without_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            declared = directory / "declared.json"
            observed = directory / "transcript.jsonl"
            output = directory / "receipt.json"
            declared.write_bytes(b"0" * (4 * 1024 * 1024 + 1))
            observed.write_bytes(b"")
            result = run_paths(declared, observed, output)
            self.assertEqual(2, result.returncode)
            self.assertFalse(output.exists())

    def test_transcript_line_count_ceiling_fails_without_receipt(self) -> None:
        observed = b'{"type":"other"}\n' * 100_001
        result, _, exists = run_case(b"0", observed)
        self.assertEqual(2, result.returncode)
        self.assertFalse(exists)

    def test_embedded_blank_line_and_bom_fail_closed(self) -> None:
        invalid = [
            transcript(event("read")) + b"\n" + transcript(event("read")),
            b"\xef\xbb\xbf0",
        ]
        for declared_or_transcript in invalid:
            with self.subTest(payload=declared_or_transcript[:20]):
                if declared_or_transcript.startswith(b"\xef"):
                    result, _, exists = run_case(declared_or_transcript, b"")
                else:
                    result, _, exists = run_case(b"2", declared_or_transcript)
                self.assertEqual(2, result.returncode)
                self.assertFalse(exists)

    def test_empty_event_type_fails_without_receipt(self) -> None:
        result, _, exists = run_case(b"0", b"", event_type="")
        self.assertEqual(2, result.returncode)
        self.assertFalse(exists)

    def test_all_nonselected_events_accept_zero(self) -> None:
        observed = transcript(
            b'{"type":"other","name":"private-nonselected"}',
            b'{"type":"status","toolName":null}',
        )
        result, payload, _ = run_case(b"0", observed)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn(b"private-nonselected", payload or b"")

    def test_scalar_receipt_has_null_declared_counts_and_hash_sort(self) -> None:
        result, payload, _ = run_case(b"2", transcript(event("zeta"), event("alpha")))
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(payload or b"null")
        per_name = receipt["perName"]
        self.assertEqual([None, None], [item["declaredCount"] for item in per_name])
        hashes = [item["toolNameSha256"] for item in per_name]
        self.assertEqual(sorted(hashes), hashes)

    def test_openclaw_message_custody_failures_produce_no_receipt(self) -> None:
        invalid = [
            b'{"type":"message","message":null}\n',
            b'{"type":"message","message":{"role":"assistant","content":null}}\n',
            b'{"type":"message","message":{"role":"assistant","content":[{}]}}\n',
            b'{"type":"message","message":{"role":"assistant","content":[{"type":"toolCall"}]}}\n',
        ]
        for observed in invalid:
            with self.subTest(observed=observed):
                result, _, exists = run_case(b"0", observed, transcript_format="openclaw")
                self.assertEqual(2, result.returncode)
                self.assertFalse(exists)


if __name__ == "__main__":
    unittest.main()
