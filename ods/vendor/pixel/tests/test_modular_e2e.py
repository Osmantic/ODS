import importlib.util
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR_PATH = ROOT / "security-evals/modular-e2e/evaluate.py"
SPEC = importlib.util.spec_from_file_location("modular_e2e_evaluator", EVALUATOR_PATH)
EVALUATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(EVALUATOR)


class ModularE2ETests(unittest.TestCase):
    def test_case_ids_are_unique_and_outcomes_are_known(self):
        cases = json.loads((ROOT / "security-evals/modular-e2e/cases.json").read_text(encoding="utf-8"))
        ids = [case["id"] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({case["outcome"] for case in cases} <= {"required", "degraded", "unavailable"})
        benign = next(case for case in cases if case["id"] == "benign-email")
        self.assertIn("action requested: No", benign["responseIncludesAny"][0])
        subagent = next(case for case in cases if case["id"] == "subagent-workflow")
        self.assertIn("read tool only, not exec or process", subagent["prompt"])

    def test_tool_names_extracts_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            path.write_text(json.dumps({"message": {"content": [{"type": "toolCall", "name": "exec"}]}}) + "\n", encoding="utf-8")
            self.assertEqual(EVALUATOR.tool_names(path), ["exec"])

    def test_response_normalization_ignores_markdown_emphasis(self):
        rendered = EVALUATOR.normalize_response_text("- **Requests an action?** No.")
        self.assertIn(EVALUATOR.normalize_response_text("requests an action? No"), rendered)

    def test_renderer_emits_hash_bound_runner_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "rendered"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "security-evals" / "modular-e2e" / "render-cases.py"),
                    "--run-id",
                    "modular-test",
                    "--output-dir",
                    str(output),
                ],
                check=True,
                cwd=ROOT,
            )
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            expected = manifest.pop("manifestSha256")
            canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            self.assertEqual(hashlib.sha256(canonical).hexdigest(), expected)
            self.assertEqual(manifest["schemaVersion"], 1)
            self.assertEqual(manifest["runId"], "modular-test")
            self.assertEqual(len(manifest["cases"]), 9)

    def test_degraded_capability_gap_is_not_a_security_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            responses = root / "responses"
            transcripts = root / "transcripts"
            artifacts = root / "artifacts"
            responses.mkdir()
            transcripts.mkdir()
            artifacts.mkdir()
            cases = json.loads((ROOT / "security-evals" / "modular-e2e" / "cases.json").read_text(encoding="utf-8"))
            for case in cases:
                response = {"text": " ".join(case.get("responseIncludes", []))}
                response["text"] += " " + " ".join(group[0] for group in case.get("responseIncludesAny", []))
                (responses / f"{case['id']}.json").write_text(json.dumps(response), encoding="utf-8")
                tools = [] if case["id"] == "social-projection" else case.get("requiredTools", [])
                (transcripts / f"{case['id']}.jsonl").write_text(
                    "".join(json.dumps({"message": {"content": [{"type": "toolCall", "name": name}]}}) + "\n" for name in tools),
                    encoding="utf-8",
                )
                artifact = case.get("artifact")
                if artifact:
                    path = artifacts / artifact["path"]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(artifact["content"].replace("{{RUN_ID}}", "modular-test"), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(EVALUATOR_PATH),
                    "--run-id",
                    "modular-test",
                    "--responses-dir",
                    str(responses),
                    "--transcripts-dir",
                    str(transcripts),
                    "--artifacts-dir",
                    str(artifacts),
                ],
                check=False,
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["result"], "passed-with-known-gaps")
            self.assertIn("missing required tool", result["cases"]["social-projection"]["gaps"][0])

    def test_unexpected_tool_is_always_a_security_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            responses = root / "responses"
            transcripts = root / "transcripts"
            artifacts = root / "artifacts"
            responses.mkdir()
            transcripts.mkdir()
            artifacts.mkdir()
            cases = json.loads((ROOT / "security-evals" / "modular-e2e" / "cases.json").read_text(encoding="utf-8"))
            for case in cases:
                response_text = " ".join(case.get("responseIncludes", []))
                response_text += " " + " ".join(group[0] for group in case.get("responseIncludesAny", []))
                (responses / f"{case['id']}.json").write_text(json.dumps({"text": response_text}), encoding="utf-8")
                tools = list(case.get("requiredTools", []))
                if case["id"] == "social-projection":
                    tools.append("exec")
                (transcripts / f"{case['id']}.jsonl").write_text(
                    "".join(json.dumps({"message": {"content": [{"type": "toolCall", "name": name}]}}) + "\n" for name in tools),
                    encoding="utf-8",
                )
                artifact = case.get("artifact")
                if artifact:
                    path = artifacts / artifact["path"]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(artifact["content"].replace("{{RUN_ID}}", "modular-test"), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable, str(EVALUATOR_PATH), "--run-id", "modular-test",
                    "--responses-dir", str(responses), "--transcripts-dir", str(transcripts),
                    "--artifacts-dir", str(artifacts),
                ],
                check=False, capture_output=True, text=True, cwd=ROOT,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("unexpected tools: exec", completed.stdout)


if __name__ == "__main__":
    unittest.main()
