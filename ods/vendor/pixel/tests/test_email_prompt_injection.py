import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR_PATH = ROOT / "security-evals/email-prompt-injection/evaluate.py"
SPEC = importlib.util.spec_from_file_location("email_injection_evaluator", EVALUATOR_PATH)
EVALUATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(EVALUATOR)
RUNNER_PATH = ROOT / "security-evals/operations-live/run-cases.py"
RUNNER_SPEC = importlib.util.spec_from_file_location("shared_case_runner", RUNNER_PATH)
RUNNER = importlib.util.module_from_spec(RUNNER_SPEC)
assert RUNNER_SPEC.loader is not None
RUNNER_SPEC.loader.exec_module(RUNNER)


class EmailPromptInjectionEvaluatorTests(unittest.TestCase):
    def render(self, output: Path) -> Path:
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "security-evals/email-prompt-injection/render-cases.py"),
                "--run-id",
                "inject-test",
                "--output-dir",
                str(output),
            ],
            check=True,
            cwd=ROOT,
        )
        return output / "manifest.json"

    def test_renderer_emits_hash_bound_shared_runner_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = self.render(Path(directory) / "rendered")
            manifest = EVALUATOR.verify_manifest(manifest_path)
            self.assertEqual(manifest["schemaVersion"], 1)
            self.assertEqual(manifest["runId"], "inject-test")
            self.assertEqual(len(manifest["manifestSha256"]), 64)
            self.assertEqual(RUNNER.verify_manifest(manifest_path)["runId"], "inject-test")

    def test_manifest_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = self.render(Path(directory) / "rendered")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["cases"][0]["subject"] += " tampered"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest hash mismatch"):
                EVALUATOR.verify_manifest(manifest_path)
            with self.assertRaisesRegex(ValueError, "manifest hash mismatch"):
                RUNNER.verify_manifest(manifest_path)

    def test_expected_cases_follow_the_corpus(self):
        cases = json.loads(
            (ROOT / "security-evals/email-prompt-injection/cases.json").read_text(encoding="utf-8")
        )
        self.assertEqual(EVALUATOR.EXPECTED_CASES, {item["id"] for item in cases})
        self.assertGreaterEqual(len(cases), 10)

    def test_flatten_strings_ignores_non_strings(self):
        self.assertEqual(
            EVALUATOR.flatten_strings({"a": ["one", {"b": "two"}], "n": 3}),
            ["one", "two"],
        )

    def test_read_events_parses_ndjson(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.ndjson"
            path.write_text(json.dumps({"case": "direct-command"}) + "\n", encoding="utf-8")
            self.assertEqual(EVALUATOR.read_events(path), [{"case": "direct-command"}])

    def test_read_events_rejects_invalid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.ndjson"
            path.write_text("not-json\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid events.ndjson line 1"):
                EVALUATOR.read_events(path)

    def test_read_tool_calls_extracts_only_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            rows = [
                {"message": {"role": "assistant", "content": [
                    {"type": "toolCall", "name": "pixel_gmail_read", "arguments": {"messageId": "test"}},
                    {"type": "text", "text": "done"},
                ]}},
                {"message": {"role": "assistant", "content": [
                    {"type": "toolCall", "name": "exec", "arguments": {"command": "true"}}
                ]}},
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            self.assertEqual(
                EVALUATOR.read_tool_calls(path),
                [
                    {"name": "pixel_gmail_read", "arguments": {"messageId": "test"}},
                    {"name": "exec", "arguments": {"command": "true"}},
                ],
            )

    def test_read_tool_calls_rejects_invalid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            path.write_text("not-json\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid transcript line 1"):
                EVALUATOR.read_tool_calls(path)


if __name__ == "__main__":
    unittest.main()
