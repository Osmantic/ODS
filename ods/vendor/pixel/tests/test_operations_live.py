import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "security-evals/operations-live"


class OperationsLiveTests(unittest.TestCase):
    def render(self, output: Path) -> Path:
        subprocess.run(
            [
                sys.executable,
                str(HARNESS / "render-cases.py"),
                "--run-id",
                "accept-test",
                "--target-a",
                "runner-a",
                "--target-b",
                "runner-b",
                "--download-url",
                "https://example.com/artifact.txt",
                "--download-sha256",
                "a" * 64,
                "--candidate-release",
                "candidate-20260805",
                "--bad-release",
                "bad-20260805",
                "--package-path",
                "/var/lib/pixel-runner/jobs/artifacts/pixel-fixture.pkg",
                "--package-sha256",
                "b" * 64,
                "--output-dir",
                str(output),
            ],
            check=True,
            cwd=ROOT,
        )
        return output / "manifest.json"

    def test_cases_are_unique_and_tool_confined(self):
        cases = json.loads((HARNESS / "cases.json").read_text(encoding="utf-8"))
        ids = [case["id"] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        for case in cases:
            self.assertTrue(set(case["requiredTools"]) <= set(case["allowedTools"]))
            self.assertNotIn("exec", case["allowedTools"])

    def test_renderer_substitutes_private_inputs_and_hashes_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = self.render(Path(directory) / "rendered")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schemaVersion"], 1)
            self.assertEqual(len(manifest["manifestSha256"]), 64)
            combined = json.dumps(manifest["cases"])
            self.assertNotIn("{{", combined)
            self.assertIn("runner-a", combined)

    def test_evaluator_accepts_confined_complete_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self.render(root / "rendered")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            responses = root / "responses"
            transcripts = root / "transcripts"
            responses.mkdir()
            transcripts.mkdir()
            for case in manifest["cases"]:
                response = {"text": " ".join(case.get("responseIncludes", []))}
                response["text"] += " " + " ".join(group[0] for group in case.get("responseIncludesAny", []))
                (responses / f"{case['id']}.json").write_text(json.dumps(response), encoding="utf-8")
                rows = [
                    {"message": {"content": [{"type": "toolCall", "name": name}]}}
                    for name in [
                        "pixel_ops_inventory",
                        *case["requiredTools"],
                        *(group[0] for group in case.get("requiredToolsAny", [])),
                    ]
                ]
                (transcripts / f"{case['id']}.jsonl").write_text(
                    "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
                )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(HARNESS / "evaluate.py"),
                    "--manifest",
                    str(manifest_path),
                    "--responses-dir",
                    str(responses),
                    "--transcripts-dir",
                    str(transcripts),
                ],
                check=False,
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["result"], "passed")

    def test_evaluator_rejects_generic_process_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self.render(root / "rendered")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            case = next(item for item in manifest["cases"] if item["id"] == "parallel-workflow")
            manifest["cases"] = [case]
            manifest.pop("manifestSha256")
            canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            manifest["manifestSha256"] = hashlib.sha256(canonical).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            responses = root / "responses"; responses.mkdir()
            transcripts = root / "transcripts"; transcripts.mkdir()
            response_text = " ".join(case.get("responseIncludes", []))
            (responses / "parallel-workflow.json").write_text(json.dumps({"text": response_text}), encoding="utf-8")
            names = [
                "pixel_ops_inventory",
                *case["requiredTools"],
                *(group[0] for group in case.get("requiredToolsAny", [])),
                "exec",
                "process",
            ]
            (transcripts / "parallel-workflow.jsonl").write_text(
                "".join(json.dumps({"message": {"content": [{"type": "toolCall", "name": name}]}}) + "\n" for name in names),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable, str(HARNESS / "evaluate.py"), "--manifest", str(manifest_path),
                    "--responses-dir", str(responses), "--transcripts-dir", str(transcripts),
                ],
                check=False, capture_output=True, text=True, cwd=ROOT,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("unexpected tools: exec, process", completed.stdout)

    def test_evaluator_accepts_equivalent_live_completion_language(self):
        live_phrasing = {
            "download-transfer": "non-executable; execution: none",
            "host-repository-pack": "all five jobs reached terminal state; each exit 0",
            "package-transaction": "all three jobs reached their expected terminal state; each exit 0",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self.render(root / "rendered")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["cases"] = [case for case in manifest["cases"] if case["id"] in live_phrasing]
            manifest.pop("manifestSha256")
            canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            manifest["manifestSha256"] = hashlib.sha256(canonical).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            responses = root / "responses"; responses.mkdir()
            transcripts = root / "transcripts"; transcripts.mkdir()
            for case in manifest["cases"]:
                response_text = " ".join(case.get("responseIncludes", []))
                response_text += " " + live_phrasing[case["id"]]
                (responses / f"{case['id']}.json").write_text(
                    json.dumps({"text": response_text}), encoding="utf-8"
                )
                names = [
                    "pixel_ops_inventory",
                    *case["requiredTools"],
                    *(group[0] for group in case.get("requiredToolsAny", [])),
                ]
                (transcripts / f"{case['id']}.jsonl").write_text(
                    "".join(
                        json.dumps({"message": {"content": [{"type": "toolCall", "name": name}]}}) + "\n"
                        for name in names
                    ),
                    encoding="utf-8",
                )
            completed = subprocess.run(
                [
                    sys.executable, str(HARNESS / "evaluate.py"), "--manifest", str(manifest_path),
                    "--responses-dir", str(responses), "--transcripts-dir", str(transcripts),
                ],
                check=False, capture_output=True, text=True, cwd=ROOT,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_lease_renderer_scopes_production_transactions(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "leases"
            subprocess.run(
                [
                    sys.executable, str(HARNESS / "render-leases.py"),
                    "--run-id", "accept-test", "--target-a", "runner-a", "--target-b", "runner-b",
                    "--candidate-release", "candidate-20260805", "--bad-release", "bad-20260805",
                    "--package-path", "/var/lib/pixel-runner/jobs/artifacts/pixel-fixture.pkg",
                    "--package-sha256", "b" * 64, "--output-dir", str(output),
                ],
                check=True, cwd=ROOT, capture_output=True, text=True,
            )
            package = json.loads((output / "package.json").read_text(encoding="utf-8"))
            self.assertEqual(package["targets"], ["runner-a"])
            self.assertEqual(package["environments"], ["production"])
            self.assertTrue(package["allowProduction"])
            self.assertEqual(package["maxConcurrent"], 1)
            self.assertEqual(package["parameterConstraints"]["sha256"]["values"], ["b" * 64])
            workflow = json.loads((output / "workflow.json").read_text(encoding="utf-8"))
            self.assertEqual(workflow["targets"], ["runner-a", "runner-b"])
            self.assertEqual(workflow["maxConcurrent"], 2)
            download = json.loads((output / "download.json").read_text(encoding="utf-8"))
            self.assertEqual(download["targets"], ["broker"])
            self.assertEqual(download["environments"], ["lab"])
            self.assertNotIn("allowProduction", download)

            retry = Path(directory) / "retry-leases"
            subprocess.run(
                [
                    sys.executable, str(HARNESS / "render-leases.py"),
                    "--run-id", "accept-test", "--lease-id-suffix", "retry-test",
                    "--target-a", "runner-a", "--target-b", "runner-b",
                    "--candidate-release", "candidate-20260805", "--bad-release", "bad-20260805",
                    "--package-path", "/var/lib/pixel-runner/jobs/artifacts/pixel-fixture.pkg",
                    "--package-sha256", "b" * 64, "--output-dir", str(retry),
                ],
                check=True, cwd=ROOT, capture_output=True, text=True,
            )
            retried_collect = json.loads((retry / "artifact-collect.json").read_text(encoding="utf-8"))
            self.assertEqual(retried_collect["id"], "accept-retry-test-artifact-collect")
            self.assertEqual(
                retried_collect["parameterConstraints"]["destination"]["values"],
                ["fixture-accept-test.log"],
            )

    def test_lease_runtime_budgets_cover_every_action_policy_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "leases"
            subprocess.run(
                [
                    sys.executable, str(HARNESS / "render-leases.py"),
                    "--run-id", "accept-test", "--target-a", "runner-a", "--target-b", "runner-b",
                    "--candidate-release", "candidate-20260805", "--bad-release", "bad-20260805",
                    "--package-path", "/var/lib/pixel-runner/jobs/artifacts/pixel-fixture.pkg",
                    "--package-sha256", "b" * 64, "--output-dir", str(output),
                ],
                check=True, cwd=ROOT, capture_output=True, text=True,
            )
            action_pack = json.loads((ROOT / "deploy/ops-broker/action-packs.example.json").read_text(encoding="utf-8"))
            for lease_path in output.glob("*.json"):
                lease = json.loads(lease_path.read_text(encoding="utf-8"))
                for action_id in lease["actions"]:
                    action = action_pack["actions"].get(action_id)
                    if action and "timeoutSeconds" in action:
                        self.assertGreaterEqual(
                            lease["maxRuntimeSeconds"],
                            action["timeoutSeconds"],
                            f"{lease_path.name} cannot authorize {action_id}: runtime budget is too small",
                        )

    @unittest.skipUnless(os.name == "posix", "live runner invokes a POSIX OpenClaw executable")
    def test_live_runner_captures_only_returned_session_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self.render(root / "rendered")
            sessions = root / "sessions"; sessions.mkdir()
            transcript = sessions / "00000000-0000-0000-0000-000000000001.jsonl"
            transcript.write_text('{"message":{"content":[{"type":"toolCall","name":"pixel_ops_inventory"}]}}\n', encoding="utf-8")
            fake = root / "openclaw"
            fake.write_text(
                "#!/usr/bin/env python3\nimport json,sys\n"
                "if '--help' in sys.argv:\n print('  --message-file <path>  --session-key <key>')\n raise SystemExit(0)\n"
                "if sys.argv[1:4] == ['agents','list','--json']:\n print(json.dumps([{'id':'pixel'}]))\n raise SystemExit(0)\n"
                "print(json.dumps({'status':'ok','result':{'meta':{'agentMeta':{'sessionFile':%r}}}}))\n" % str(transcript),
                encoding="utf-8",
            )
            fake.chmod(0o755)
            responses, transcripts = root / "responses", root / "transcripts"
            completed = subprocess.run(
                [
                    sys.executable, str(HARNESS / "run-cases.py"), "--manifest", str(manifest_path),
                    "--openclaw-bin", str(fake), "--agent", "pixel", "--session-prefix", "accept-test",
                    "--cases", "inventory", "--responses-dir", str(responses),
                    "--transcripts-dir", str(transcripts), "--sessions-root", str(sessions),
                ],
                check=False, capture_output=True, text=True, cwd=ROOT,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue((responses / "inventory.json").is_file())
            self.assertEqual((transcripts / "inventory.jsonl").read_text(encoding="utf-8"), transcript.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "posix", "live runner invokes a POSIX OpenClaw executable")
    def test_live_runner_waits_for_yielded_parent_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self.render(root / "rendered")
            sessions = root / "sessions"; sessions.mkdir()
            transcript = sessions / "00000000-0000-0000-0000-000000000002.jsonl"
            transcript.write_text(
                json.dumps({"message": {"role": "assistant", "content": [
                    {"type": "toolCall", "name": "sessions_yield", "arguments": {}}
                ]}}) + "\n",
                encoding="utf-8",
            )
            fake = root / "openclaw"
            append_code = (
                "import json,time; time.sleep(0.2); "
                f"p={str(transcript)!r}; "
                "open(p,'a',encoding='utf-8').write(json.dumps({'message':{'role':'assistant','api':'openai-completions','content':[{'type':'text','text':'child delivered'}]}})+'\\n')"
            )
            fake.write_text(
                "#!/usr/bin/env python3\nimport json,subprocess,sys\n"
                "if '--help' in sys.argv:\n print('  --message-file <path>  --session-key <key>')\n raise SystemExit(0)\n"
                "if sys.argv[1:4] == ['agents','list','--json']:\n print(json.dumps([{'id':'pixel'}]))\n raise SystemExit(0)\n"
                f"subprocess.Popen([sys.executable,'-c',{append_code!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)\n"
                "print(json.dumps({'status':'ok','result':{'payloads':[],'meta':{'agentMeta':{'sessionFile':%r}}}}))\n" % str(transcript),
                encoding="utf-8",
            )
            fake.chmod(0o755)
            responses, transcripts = root / "responses", root / "transcripts"
            completed = subprocess.run(
                [
                    sys.executable, str(HARNESS / "run-cases.py"), "--manifest", str(manifest_path),
                    "--openclaw-bin", str(fake), "--agent", "pixel", "--session-prefix", "accept-test",
                    "--cases", "inventory", "--responses-dir", str(responses),
                    "--transcripts-dir", str(transcripts), "--sessions-root", str(sessions), "--timeout", "30",
                ],
                check=False, capture_output=True, text=True, cwd=ROOT,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            captured = (transcripts / "inventory.jsonl").read_text(encoding="utf-8")
            self.assertIn("child delivered", captured)
            response = json.loads((responses / "inventory.json").read_text(encoding="utf-8"))
            self.assertEqual(response["result"]["payloads"][0]["text"], "child delivered")

    def test_terminal_assistant_text_ignores_cli_wait_echo(self):
        records = [
            {"message": {"role": "assistant", "content": [
                {"type": "toolCall", "name": "sessions_yield"}
            ]}},
            {"message": {"role": "assistant", "api": "cli", "content": [
                {"type": "text", "text": "still waiting"}
            ]}},
        ]
        spec = importlib.util.spec_from_file_location("operations_live_runner", HARNESS / "run-cases.py")
        runner = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(runner)
        self.assertIsNone(runner.terminal_assistant_text(records, 0))
        records.append({"message": {"role": "assistant", "api": "openai-completions", "content": [
            {"type": "text", "text": "child delivered"}
        ]}})
        self.assertEqual(runner.terminal_assistant_text(records, 0), "child delivered")

    @unittest.skipUnless(os.name == "posix", "live runner invokes a POSIX OpenClaw executable")
    def test_live_runner_preflights_launcher_before_evidence_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self.render(root / "rendered")
            fake = root / "openclaw"
            fake.write_text("#!/bin/sh\necho incompatible launcher >&2\nexit 2\n", encoding="utf-8")
            fake.chmod(0o755)
            responses, transcripts = root / "responses", root / "transcripts"
            completed = subprocess.run(
                [
                    sys.executable, str(HARNESS / "run-cases.py"), "--manifest", str(manifest_path),
                    "--openclaw-bin", str(fake), "--agent", "pixel", "--session-prefix", "accept-test",
                    "--cases", "inventory", "--responses-dir", str(responses),
                    "--transcripts-dir", str(transcripts),
                ],
                check=False, capture_output=True, text=True, cwd=ROOT,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("launcher preflight failed", completed.stderr)
            self.assertFalse(responses.exists())
            self.assertFalse(transcripts.exists())

    @unittest.skipUnless(os.name == "posix", "live runner invokes a POSIX OpenClaw executable")
    def test_live_runner_rejects_wrong_agent_state_before_evidence_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self.render(root / "rendered")
            fake = root / "openclaw"
            fake.write_text(
                "#!/bin/sh\n"
                "case \" $* \" in\n"
                "  *' agent --help '*) printf '%s\\n' '  --message-file <path>  --session-key <key>' ;;\n"
                "  *' agents list --json '*) printf '%s\\n' '[{\"id\":\"other\"}]' ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            responses, transcripts = root / "responses", root / "transcripts"
            completed = subprocess.run(
                [
                    sys.executable, str(HARNESS / "run-cases.py"), "--manifest", str(manifest_path),
                    "--openclaw-bin", str(fake), "--agent", "pixel", "--session-prefix", "accept-test",
                    "--cases", "inventory", "--responses-dir", str(responses),
                    "--transcripts-dir", str(transcripts),
                ],
                check=False, capture_output=True, text=True, cwd=ROOT,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn('could not find "pixel"', completed.stderr)
            self.assertFalse(responses.exists())
            self.assertFalse(transcripts.exists())

    @unittest.skipUnless(os.name == "posix", "live runner invokes a POSIX OpenClaw executable")
    def test_live_runner_rejects_launcher_without_exact_session_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = self.render(root / "rendered")
            fake = root / "openclaw"
            fake.write_text(
                "#!/bin/sh\nprintf '%s\\n' '  --message-file <path>' '  --session-id <id>'\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            responses, transcripts = root / "responses", root / "transcripts"
            completed = subprocess.run(
                [
                    sys.executable, str(HARNESS / "run-cases.py"), "--manifest", str(manifest_path),
                    "--openclaw-bin", str(fake), "--agent", "pixel", "--session-prefix", "accept-test",
                    "--cases", "inventory", "--responses-dir", str(responses),
                    "--transcripts-dir", str(transcripts),
                ],
                check=False, capture_output=True, text=True, cwd=ROOT,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("does not advertise --session-key", completed.stderr)
            self.assertFalse(responses.exists())
            self.assertFalse(transcripts.exists())


if __name__ == "__main__":
    unittest.main()
