from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "tool_accounting_reconcile.py"


def event(name: str, **extra: object) -> bytes:
    value = {"type": "tool-call", "name": name, **extra}
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def transcript(*lines: bytes) -> bytes:
    return b"\n".join(lines) + (b"\n" if lines else b"")


def run_case(
    declared: bytes,
    observed: bytes,
    *,
    event_type: str = "tool-call",
    declared_format: str = "accounting",
    transcript_format: str = "events",
    preexisting_output: bytes | None = None,
) -> tuple[subprocess.CompletedProcess[bytes], bytes | None, bool]:
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        declared_path = directory / "declared.json"
        transcript_path = directory / "transcript.jsonl"
        output_path = directory / "receipt.json"
        declared_path.write_bytes(declared)
        transcript_path.write_bytes(observed)
        if preexisting_output is not None:
            output_path.write_bytes(preexisting_output)
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--declared",
                str(declared_path),
                "--declared-format",
                declared_format,
                "--transcript",
                str(transcript_path),
                "--event-type",
                event_type,
                "--transcript-format",
                transcript_format,
                "--output",
                str(output_path),
            ],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        exists = output_path.exists()
        payload = output_path.read_bytes() if exists else None
        return result, payload, exists


class ToolAccountingReconcileTests(unittest.TestCase):
    def receipt(self, payload: bytes | None) -> dict[str, object]:
        self.assertIsNotNone(payload)
        assert payload is not None
        return json.loads(payload)

    def test_pxl102_total_mismatch_rejected(self) -> None:
        result, payload, _ = run_case(b"4", transcript(*(event("read") for _ in range(5))))
        self.assertEqual(1, result.returncode, result.stderr)
        receipt = self.receipt(payload)
        self.assertEqual("reject", receipt["decision"])
        self.assertEqual(["total-mismatch"], receipt["mismatchCodes"])
        self.assertEqual((4, 5), (receipt["declaredTotal"], receipt["transcriptTotal"]))

    def test_pxl105_equal_total_wrong_distribution_rejected(self) -> None:
        declared = b'{"calls":{"read":2,"shell":1}}'
        observed = transcript(event("read"), event("read"), event("edit"))
        result, payload, _ = run_case(declared, observed)
        self.assertEqual(1, result.returncode, result.stderr)
        codes = self.receipt(payload)["mismatchCodes"]
        self.assertEqual(
            ["declared-only-name", "name-distribution-mismatch", "transcript-only-name"],
            codes,
        )

    def test_pxl103_scalar_match_accepted(self) -> None:
        observed = transcript(event("read"), event("write"), event("read"), event("shell"), event("read"), event("edit"))
        result, payload, _ = run_case(b"6", observed)
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = self.receipt(payload)
        self.assertEqual("accept", receipt["decision"])
        self.assertEqual([], receipt["mismatchCodes"])

    def test_pxl104_records_sum_duplicates_and_explicit_counts(self) -> None:
        declared = b'[{"tool":"read","count":2},{"toolName":"read"},{"name":"edit","count":2}]'
        observed = transcript(event("read"), event("edit"), event("read"), event("edit"), event("read"))
        result, payload, _ = run_case(declared, observed)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("records", self.receipt(payload)["declaredShape"])

    def test_real_checkpoint_and_openclaw_shapes(self) -> None:
        def openclaw(*names: str) -> bytes:
            content = [
                {"type": "toolCall", "name": name, "arguments": {"private": "must-not-project"}}
                for name in names
            ]
            record = {"type": "message", "message": {"role": "assistant", "content": content}}
            return json.dumps(record, separators=(",", ":")).encode() + b"\n"

        cases = [
            (
                {
                    "trialId": "PXL-LIVE-102",
                    "toolCalls": {"counts": {"search": 4, "thread": 10, "calendar": 1, "write": 1, "total": 16}},
                },
                openclaw(*(["search"] * 4 + ["thread"] * 9 + ["calendar", "write"])),
                1,
                "checkpoint-counts",
            ),
            (
                {
                    "trialId": "PXL-LIVE-103",
                    "toolCalls": {
                        "counts": {
                            "search": {"calls": 4, "pagesFetched": 4},
                            "thread": {"calls": 10, "successful": 10},
                            "calendar": {"calls": 1},
                            "write": {"calls": 1},
                            "total": 16,
                        }
                    },
                },
                openclaw(*(["search"] * 4 + ["thread"] * 10 + ["calendar", "write"])),
                0,
                "checkpoint-counts",
            ),
            (
                {
                    "trialId": "PXL-LIVE-104",
                    "toolCalls": [
                        {"tool": "read", "count": 1, "params": {"private": "ignored"}},
                        {"tool": "stage", "count": 1, "outcome": "ignored"},
                        {"tool": "get", "count": 1},
                        {"tool": "events", "count": 1},
                        {"tool": "write", "count": 1},
                    ],
                },
                openclaw("read", "stage", "get", "events", "write"),
                0,
                "checkpoint-records",
            ),
            (
                {
                    "trialId": "PXL-LIVE-105",
                    "toolCalls": {
                        "read": {"count": 1, "files": ["private"]},
                        "stage": {"count": 1, "calls": ["private metadata"]},
                        "get": {"count": 1},
                        "events": {"count": 1},
                        "write": {"count": 1},
                    },
                },
                openclaw("read", "read", "stage", "get", "events", "write"),
                1,
                "checkpoint-map",
            ),
        ]
        for checkpoint, observed, expected, shape in cases:
            with self.subTest(trial=checkpoint["trialId"]):
                result, payload, _ = run_case(
                    json.dumps(checkpoint, separators=(",", ":")).encode(),
                    observed,
                    declared_format="checkpoint",
                    transcript_format="openclaw",
                )
                self.assertEqual(expected, result.returncode, result.stderr)
                receipt = self.receipt(payload)
                self.assertEqual(shape, receipt["declaredShape"])
                self.assertEqual("checkpoint", receipt["declaredFormat"])
                self.assertEqual("openclaw", receipt["transcriptFormat"])
                self.assertNotIn(b"must-not-project", payload or b"")

    def test_checkpoint_internal_ambiguity_and_total_drift_fail_closed(self) -> None:
        invalid = [
            {"toolCalls": {"counts": {"read": 1, "total": 2}}},
            {"toolCalls": {"read": {"calls": 1, "count": 1}}},
            {"toolCalls": [{"tool": "read", "name": "read", "count": 1}]},
            {"toolCalls": {"read": {"count": True}}},
        ]
        for checkpoint in invalid:
            with self.subTest(checkpoint=checkpoint):
                result, _, exists = run_case(
                    json.dumps(checkpoint, separators=(",", ":")).encode(),
                    b"",
                    declared_format="checkpoint",
                    transcript_format="openclaw",
                )
                self.assertEqual(2, result.returncode)
                self.assertFalse(exists)

    def test_all_admitted_shapes(self) -> None:
        cases = [
            (b"1", "scalar"),
            (b'{"calls":1}', "calls"),
            (b'{"count":1}', "count"),
            (b'{"calls":{"read":1}}', "calls-map"),
            (b'[{"name":"read"}]', "records"),
        ]
        for declared, shape in cases:
            with self.subTest(shape=shape):
                result, payload, _ = run_case(declared, transcript(event("read")))
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(shape, self.receipt(payload)["declaredShape"])

    def test_empty_named_shapes_match_empty_transcript(self) -> None:
        for declared in (b'{"calls":{}}', b"[]"):
            with self.subTest(declared=declared):
                result, _, _ = run_case(declared, b"")
                self.assertEqual(0, result.returncode, result.stderr)

    def test_invalid_declared_shapes_fail_without_receipt(self) -> None:
        invalid = [
            b"null",
            b"true",
            b"1.0",
            b"-1",
            b'"1"',
            b"{}",
            b'{"calls":1,"count":1}',
            b'{"calls":null}',
            b'{"count":true}',
            b'{"calls":{"read":-1}}',
            b'{"calls":{"read":1.0}}',
            b'{"unknown":1}',
            b'{"calls":1,"calls":2}',
            b'{"calls":NaN}',
        ]
        for declared in invalid:
            with self.subTest(declared=declared):
                result, payload, exists = run_case(declared, b"")
                self.assertEqual(2, result.returncode)
                self.assertFalse(exists)
                self.assertIsNone(payload)

    def test_invalid_declared_records_fail_without_receipt(self) -> None:
        invalid = [
            b"[1]",
            b"[{}]",
            b'[{"tool":""}]',
            b'[{"tool":"read","name":"read"}]',
            b'[{"tool":"read","count":true}]',
            b'[{"tool":"read","count":-1}]',
            b'[{"tool":"read","count":1.0}]',
            b'[{"tool":"read","extra":1}]',
            b'[{"tool":"read","tool":"write"}]',
        ]
        for declared in invalid:
            with self.subTest(declared=declared):
                result, _, exists = run_case(declared, b"")
                self.assertEqual(2, result.returncode)
                self.assertFalse(exists)

    def test_invalid_transcripts_fail_without_receipt(self) -> None:
        invalid = [
            b"\n",
            b"not-json\n",
            b"1\n",
            b"{}\n",
            b'{"type":"tool-call"}\n',
            b'{"type":"tool-call","name":""}\n',
            b'{"type":"tool-call","name":"read","tool":"read"}\n',
            b'{"type":"tool-call","name":"read","name":"write"}\n',
            b'{"type":"tool-call","name":"read","value":NaN}\n',
        ]
        for observed in invalid:
            with self.subTest(observed=observed[:80]):
                result, _, exists = run_case(b"0", observed)
                self.assertEqual(2, result.returncode)
                self.assertFalse(exists)

    def test_oversized_transcript_line_fails_closed(self) -> None:
        observed = b'{"type":"other","padding":"' + (b"x" * (256 * 1024)) + b'"}\n'
        result, _, exists = run_case(b"0", observed)
        self.assertEqual(2, result.returncode)
        self.assertFalse(exists)

    def test_nonselected_events_are_validated_but_not_counted(self) -> None:
        observed = transcript(
            b'{"type":"other","name":null,"arguments":{"private":"canary"}}',
            event("read"),
        )
        result, _, _ = run_case(b"1", observed)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_zero_over_under_and_swapped_names(self) -> None:
        result, _, _ = run_case(b"0", b"")
        self.assertEqual(0, result.returncode)
        result, _, _ = run_case(b"2", transcript(event("read")))
        self.assertEqual(1, result.returncode)
        result, _, _ = run_case(b"1", transcript(event("read"), event("read")))
        self.assertEqual(1, result.returncode)
        result, payload, _ = run_case(
            b'{"calls":{"alpha":2,"beta":1}}',
            transcript(event("alpha"), event("beta"), event("beta")),
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("name-distribution-mismatch", self.receipt(payload)["mismatchCodes"])

    def test_custom_event_type_is_selected_by_digest_only(self) -> None:
        observed = transcript(b'{"type":"custom-private-selector","tool":"read"}', event("ignored"))
        result, payload, _ = run_case(b"1", observed, event_type="custom-private-selector")
        self.assertEqual(0, result.returncode, result.stderr)
        assert payload is not None
        self.assertNotIn(b"custom-private-selector", payload)

    def test_receipt_is_deterministic_and_private(self) -> None:
        private_tool = "ultra_private_mailbox_tool"
        private_argument = "mailbox-secret-canary-8488"
        private_path = "/home/owner/private/message.txt"
        declared = json.dumps({"calls": {private_tool: 1}}, separators=(",", ":")).encode()
        observed = transcript(
            event(private_tool, arguments={"secret": private_argument, "path": private_path}, result="private-result")
        )
        first, first_payload, _ = run_case(declared, observed)
        second, second_payload, _ = run_case(declared, observed)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(first_payload, second_payload)
        assert first_payload is not None
        for forbidden in (private_tool, private_argument, private_path, "private-result", "tool-call"):
            self.assertNotIn(forbidden.encode(), first_payload)
        self.assertTrue(first_payload.endswith(b"\n"))

    def test_output_refuses_overwrite_and_preserves_existing_bytes(self) -> None:
        original = b"owner-existing-data"
        result, payload, exists = run_case(b"0", b"", preexisting_output=original)
        self.assertEqual(2, result.returncode)
        self.assertTrue(exists)
        self.assertEqual(original, payload)

    @unittest.skipIf(os.name == "nt", "POSIX mode assertion")
    def test_receipt_mode_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            declared = directory / "declared.json"
            observed = directory / "transcript.jsonl"
            output = directory / "receipt.json"
            declared.write_bytes(b"0")
            observed.write_bytes(b"")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--declared", str(declared), "--transcript", str(observed), "--output", str(output)],
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(0o600, output.stat().st_mode & 0o777)


if __name__ == "__main__":
    unittest.main()
