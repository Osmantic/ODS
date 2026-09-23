import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portal_outcome_materialize_battery as materializer


def payload(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def contracts():
    model = {
        "$schema": materializer.outcome_task.MODEL_SCHEMA, "schemaVersion": 1,
        "operation": "pixel-portal-outcome-model-contract", "modelId": "DeepSeek-V4-Flash-0731",
        "artifact": {
            "kind": "directory-manifest", "sha256": "1" * 64, "bytes": 4096, "fileCount": 2,
            "format": "safetensors", "quantization": "FP8", "tokenizerSha256": "2" * 64,
            "chatTemplateSha256": "3" * 64, "metadataSha256": "4" * 64,
        },
        "runtime": {
            "implementation": "vllm", "imageDigest": "sha256:" + "5" * 64,
            "executableSha256": "6" * 64, "launchArgumentsSha256": "7" * 64,
            "protocol": "openai-chat-completions-v1", "contextWindow": 131072, "parallelSlots": 1,
            "resources": {"acceleratorClass": "nvidia", "acceleratorCount": 1, "cpuCores": 16, "memoryMiB": 131072, "sharedMemoryMiB": 16384, "tmpfsMiB": 1024, "cacheMiB": 16384, "pidsLimit": 4096},
            "runtimeIsolation": "fresh-per-run", "restartPolicy": "no", "crossRunStateAllowed": False,
        },
        "authority": {"grantsModelStart": False, "grantsProviderCall": False, "grantsNetwork": False, "grantsCredentialUse": False, "grantsExecution": False, "grantsCompletion": False},
        "boundary": materializer.outcome_task.MODEL_BOUNDARY,
    }
    inference = {
        "$schema": materializer.outcome_task.INFERENCE_SCHEMA, "schemaVersion": 1,
        "operation": "pixel-portal-outcome-inference-contract",
        "sampling": {"source": "request-boundary-enforced", "temperaturePermille": 700, "topPPermille": 950, "topK": 40, "minPPermille": 50, "repeatPenaltyPermille": 1100, "seed": 42, "reasoningEffort": "backend-default", "reasoningVisibility": "hidden"},
        "request": {"wireApi": "openai-chat-completions", "stream": True, "maxOutputTokens": 4096, "toolEncoding": "function", "requestFieldPolicySha256": "8" * 64, "promptCachePolicy": "empty-at-run-start"},
        "authority": {"grantsInference": False, "grantsToolUse": False, "grantsExecution": False, "grantsCompletion": False},
        "boundary": materializer.outcome_task.INFERENCE_BOUNDARY,
    }
    inference["request"]["requestFieldPolicySha256"] = materializer.outcome_task.inference_policy_sha256(inference)
    return payload(model), payload(inference)


def tree_identity(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


class PortalOutcomeBatteryMaterializationTests(unittest.TestCase):
    def test_disabled_builder_template_can_admit_maximum_quality_without_widening_authority(self):
        policy = json.loads((ROOT / "deploy/work-broker/policy.example.json").read_text(encoding="utf-8"))
        battery = json.loads((ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_text(encoding="utf-8"))
        ceiling = policy["profiles"]["builder"]["maxBudgets"]
        quality = materializer.EVALUATION_REGIMES["maximum-quality"]
        required = {
            "maxRuntimeSeconds": max(
                task["wallSeconds"] for task in battery["tasks"] if task.get("profile", "builder") == "builder"
            ) * quality["wallMultiplier"],
            "maxIterations": quality["maxIterations"],
            "maxToolCalls": quality["maxToolCalls"],
            "maxConcurrentSubagents": quality["maxConcurrentSubagents"],
            "maxModelRequests": quality["modelRequests"],
            "maxInputTokens": quality["inputTokens"],
            "maxOutputTokens": quality["outputTokens"],
            "maxArtifactBytes": quality["artifactBytes"],
            "maxNetworkBytes": quality["maxNetworkBytes"],
            "maxFailures": quality["maxFailures"],
            "noProgressLimit": quality["noProgressLimit"],
        }
        self.assertTrue(all(ceiling[field] >= value for field, value in required.items()))
        self.assertGreaterEqual(policy["maxLeaseSeconds"], required["maxRuntimeSeconds"])
        self.assertFalse(policy["enabled"])
        self.assertFalse(policy["profiles"]["builder"]["enabled"])
        self.assertEqual(policy["profiles"]["builder"]["isolation"], "hardened-container")
        self.assertEqual(policy["profiles"]["builder"]["services"], ["local-model"])
        for field in (
            "directNetwork", "hostFilesystem", "dockerSocket", "sshAgent", "ambientCredentials",
            "automaticMerge", "automaticDeployment",
        ):
            self.assertIs(policy["security"][field], False)

    def test_all_curated_tasks_materialize_deterministically_and_admit(self):
        battery = (ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_bytes()
        model, inference = contracts()
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            first = parent / "first"
            second = parent / "second"
            one = materializer.materialize(
                root=ROOT, battery_payload=battery, model_payload=model, inference_payload=inference,
                verifier_image_digest="sha256:" + "e" * 64, output_root=first,
            )
            two = materializer.materialize(
                root=ROOT, battery_payload=battery, model_payload=model, inference_payload=inference,
                verifier_image_digest="sha256:" + "e" * 64, output_root=second,
            )
            expected_builder = sum(task.get("profile", "builder") == "builder" for task in json.loads(battery)["tasks"])
            self.assertEqual(len(one["tasks"]), expected_builder)
            self.assertGreaterEqual(len(one["tasks"]), 58)
            self.assertEqual(one, two)
            self.assertEqual(tree_identity(first), tree_identity(second))
            injection = json.loads((first / "battery-injection-slugify/verifier.json").read_text(encoding="utf-8"))
            self.assertIn("source/.env", injection["workspaceVerification"]["immutablePathPrefixes"])
            strict = json.loads((first / "battery-mean-to-file-strict/verifier.json").read_text(encoding="utf-8"))
            self.assertEqual(strict["finalReply"]["sha256"], hashlib.sha256(b"DONE").hexdigest())
            self.assertIn("final-reply-exact", {item["check"] for item in strict["checks"]})
            rehearsal = json.loads((first / "trial-calendar-unknown-rehearsal/verifier.json").read_text(encoding="utf-8"))
            self.assertIn("source/.pixel-scenario/", rehearsal["workspaceVerification"]["immutablePathPrefixes"])
            inventory = next(item for item in one["tasks"] if item["batteryTaskId"] == "trial-calendar-unknown-rehearsal")
            self.assertEqual(inventory["proofClass"], materializer.REHEARSAL_PROOF_CLASS)
            self.assertEqual(inventory["rehearsalJourneyId"], "calendar-unknown-outcome")
            self.assertEqual(inventory["rehearsalFault"], "timeout-after-submit")
            self.assertEqual(inventory["executionProfile"], "builder")
            self.assertEqual(inventory["targetProfile"], "assistant")
            self.assertEqual(inventory["profileFidelity"], "surrogate-rehearsal")
            repository_inventory = next(item for item in one["tasks"] if item["batteryTaskId"] == "battery-repo-atomic-checkpoint")
            self.assertEqual(repository_inventory["scaleClass"], "repository")
            self.assertGreaterEqual(repository_inventory["workspaceFiles"], 7)
            self.assertGreaterEqual(repository_inventory["workspaceBytes"], 2500)
            self.assertEqual(repository_inventory["workspaceLanguages"], ["python", "json", "markdown"])
            formal_inventory = next(item for item in one["tasks"] if item["batteryTaskId"] == "battery-trial-deep-repo-audit")
            self.assertEqual(formal_inventory["proofClass"], materializer.FORMAL_PRODUCT_TASK_PROOF_CLASS)
            self.assertEqual(formal_inventory["formalProductJourneyId"], "deep-repository-audit")
            self.assertEqual(formal_inventory["trialJourneyIds"], ["audit-plan-authoring", "plan-self-critique-harden"])
            self.assertEqual(formal_inventory["profileFidelity"], "exact")
            formal_task = json.loads((first / "battery-trial-deep-repo-audit/task.json").read_text(encoding="utf-8"))
            self.assertEqual(
                (formal_task["journeyId"], formal_task["profile"], formal_task["dataClass"], formal_task["effectBoundary"]),
                ("deep-repository-audit", "builder", "internal", "read-only"),
            )
            formal_verifier = json.loads((first / "battery-trial-deep-repo-audit/verifier.json").read_text(encoding="utf-8"))
            immutable = formal_verifier["workspaceVerification"]["immutablePathPrefixes"]
            self.assertIn("source/service/server.py", immutable)
            self.assertIn("source/deploy/service.env", immutable)
            verifier = json.loads((first / "battery-argparse-numeric-mean/verifier.json").read_text(encoding="utf-8"))
            semantic = next(check for check in verifier["workspaceVerification"]["checks"] if check["kind"] == "command")
            self.assertFalse(any(any(ord(character) < 32 or ord(character) == 127 for character in argument) for argument in semantic["argv"]))
            verification_source = parent / "verification-source"
            verification_source.mkdir()
            with tarfile.open(first / "battery-argparse-numeric-mean/source.tar", mode="r:") as archive:
                archive.extractall(verification_source, filter="data")
            completed = subprocess.run(
                [sys.executable, *semantic["argv"][1:]], cwd=verification_source,
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(completed.returncode, 0, "the verifier must reject the intentionally unfixed input")
            self.assertIn("<pixel-verifier>", completed.stderr)

    def test_maximum_quality_expands_only_bounded_resources_and_preserves_task_authority(self):
        battery = (ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_bytes()
        model, inference = contracts()
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            matched_root, quality_root = parent / "matched", parent / "quality"
            matched = materializer.materialize(
                root=ROOT, battery_payload=battery, model_payload=model, inference_payload=inference,
                verifier_image_digest="sha256:" + "e" * 64, output_root=matched_root,
                evaluation_regime="matched-budget",
            )
            quality = materializer.materialize(
                root=ROOT, battery_payload=battery, model_payload=model, inference_payload=inference,
                verifier_image_digest="sha256:" + "e" * 64, output_root=quality_root,
                evaluation_regime="maximum-quality",
            )
            self.assertEqual((matched["evaluationRegime"], quality["evaluationRegime"]), ("matched-budget", "maximum-quality"))
            task_id = "battery-repo-atomic-checkpoint"
            matched_task = json.loads((matched_root / task_id / "task.json").read_text(encoding="utf-8"))
            quality_task = json.loads((quality_root / task_id / "task.json").read_text(encoding="utf-8"))
            matched_environment = json.loads((matched_root / task_id / "environment.json").read_text(encoding="utf-8"))
            quality_environment = json.loads((quality_root / task_id / "environment.json").read_text(encoding="utf-8"))
            matched_tools = json.loads((matched_root / task_id / "tools.json").read_text(encoding="utf-8"))
            quality_tools = json.loads((quality_root / task_id / "tools.json").read_text(encoding="utf-8"))
            for field in ("journeyId", "comparisonLane", "profile", "dataClass", "effectBoundary", "scenario", "capabilities", "dataRoute", "authority", "boundary"):
                self.assertEqual(matched_task[field], quality_task[field])
            for binding in ("userRequest", "sourceSnapshot", "verifier", "sharedModelContract", "sharedInferenceContract"):
                self.assertEqual(matched_task["bindings"][binding], quality_task["bindings"][binding])
            self.assertEqual(quality_task["budgets"]["wallTimeSeconds"], matched_task["budgets"]["wallTimeSeconds"] * 8)
            self.assertEqual(quality_task["budgets"]["modelRequests"], 512)
            self.assertEqual(quality_environment["limits"]["maxIterations"], 128)
            self.assertEqual(quality_environment["limits"]["maxConcurrentSubagents"], 4)
            self.assertEqual(quality_tools["maximumSubagents"], 4)
            self.assertEqual(matched_tools["tools"], quality_tools["tools"])
            self.assertEqual(matched_tools["brokeredServices"], quality_tools["brokeredServices"])
            for field in ("hostAccess", "ambientCredentials", "externalEffects", "mergeAuthority", "deployAuthority", "policyMutation"):
                self.assertIs(quality_tools[field], False)
            self.assertEqual(quality_task["budgets"]["externalWrites"], 0)

            tampered = dict(quality_task["budgets"])
            tampered["externalWrites"] = 1
            with self.assertRaisesRegex(materializer.evaluation.OutcomeError, "resources or authority"):
                materializer.validate_regime_bindings(
                    "maximum-quality", {**quality_task, "budgets": tampered}, quality_environment, quality_tools,
                )

    def test_inline_verifier_transport_preserves_python_arguments_without_control_bytes(self):
        command = ["python3", "-c", "import sys\nassert sys.argv[1:] == ['alpha', 'beta']\nprint('OK')\n", "alpha", "beta"]
        argv = materializer.verifier_argv(command)
        self.assertFalse(any(any(ord(character) < 32 or ord(character) == 127 for character in argument) for argument in argv))
        completed = subprocess.run([sys.executable, *argv[1:]], capture_output=True, text=True, check=False)
        self.assertEqual((completed.returncode, completed.stdout), (0, "OK\n"), completed.stderr)

    def test_non_product_model_and_workspace_escape_fail_closed(self):
        model, inference = contracts()
        legacy = json.loads(model)
        legacy["modelId"] = "Qwen2.5-Coder-7B"
        battery = {
            "$schema": materializer.BATTERY_SCHEMA, "schemaVersion": 1,
            "operation": "pixel-agent-comparison-task-battery", "provenance": "test",
            "tasks": [{"taskId": "battery-escape-check", "axis": "security", "source": "test", "prompt": "Fix it", "expectReply": None, "wallSeconds": 30, "workspace": {"../escape": "no"}, "verify": {"command": ["python3", "-c", "print('OK')"], "expectStdout": "OK\n"}}],
            "boundary": materializer.BATTERY_BOUNDARY,
        }
        with self.assertRaises(materializer.evaluation.OutcomeError):
            materializer.validate_battery(payload(battery))
        good = json.loads(json.dumps(battery))
        good["tasks"][0]["workspace"] = {"app.py": "print('x')\n"}
        good["tasks"][0]["source"] = "curated-workflow"
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(materializer.evaluation.OutcomeError, "exact DSV4"):
                materializer.materialize(
                    root=ROOT, battery_payload=payload(good), model_payload=payload(legacy), inference_payload=inference,
                    verifier_image_digest="sha256:" + "e" * 64, output_root=Path(temporary) / "out",
                )

    def test_repository_scale_claims_fail_closed_when_the_workspace_is_smaller(self):
        battery = {
            "$schema": materializer.BATTERY_SCHEMA, "schemaVersion": 1,
            "operation": "pixel-agent-comparison-task-battery", "provenance": "test fixture",
            "tasks": [{
                "taskId": "battery-scale-check", "axis": "repository", "source": "curated-workflow",
                "prompt": "Repair this deliberately undersized repository fixture.", "expectReply": None,
                "wallSeconds": 60, "workspace": {f"dir{index}/file.py": "pass\n" for index in range(5)},
                "scale": {"class": "repository", "minimumFiles": 6, "minimumBytes": 1000, "languages": ["python"]},
                "verify": {"command": ["python3", "-c", "import pathlib; print('OK')"], "expectStdout": "OK"},
            }],
            "boundary": materializer.BATTERY_BOUNDARY,
        }
        with self.assertRaisesRegex(materializer.evaluation.OutcomeError, "repository scale"):
            materializer.validate_battery(payload(battery))

    def test_immutable_paths_and_formal_trial_profile_bindings_fail_closed(self):
        battery = json.loads((ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_text(encoding="utf-8"))
        formal = next(item for item in battery["tasks"] if item["taskId"] == "battery-trial-deep-repo-audit")
        formal["immutableWorkspace"] = [*formal["immutableWorkspace"], "missing/not-admitted.py"]
        with self.assertRaisesRegex(materializer.evaluation.OutcomeError, "immutable workspace"):
            materializer.validate_battery(payload(battery))

        battery = json.loads((ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_text(encoding="utf-8"))
        formal = next(item for item in battery["tasks"] if item["taskId"] == "battery-trial-deep-repo-audit")
        formal["trialProof"]["productJourneyId"] = "cited-current-research"
        with self.assertRaisesRegex(materializer.evaluation.OutcomeError, "formal product task route"):
            materializer.validate_battery(payload(battery))

    def test_researcher_profile_materializes_as_a_separate_exact_product_route(self):
        battery = (ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_bytes()
        model, inference = contracts()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "researcher"
            result = materializer.materialize(
                root=ROOT, battery_payload=battery, model_payload=model, inference_payload=inference,
                verifier_image_digest="sha256:" + "e" * 64, output_root=output, profile="researcher",
            )
            self.assertEqual(result["profile"], "researcher")
            self.assertEqual(len(result["tasks"]), 3)
            task = json.loads((output / "trial-cited-research-rehearsal/task.json").read_text(encoding="utf-8"))
            verifier = json.loads((output / "trial-cited-research-rehearsal/verifier.json").read_text(encoding="utf-8"))
            tools = json.loads((output / "trial-cited-research-rehearsal/tools.json").read_text(encoding="utf-8"))
            self.assertEqual((task["profile"], task["journeyId"], task["dataRoute"]), ("researcher", "cited-current-research", "brokered-public"))
            self.assertIsNone(verifier["workspaceVerification"])
            self.assertIn("research-inline-citations", {item["check"] for item in verifier["checks"]})
            self.assertIn("public-research", tools["tools"])
            self.assertEqual(tools["brokeredServices"], ["local-model", "public-research"])

    def test_controller_profile_materializes_as_durable_local_builder_work_not_a_surrogate(self):
        battery = (ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_bytes()
        model, inference = contracts()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "controller"
            result = materializer.materialize(
                root=ROOT, battery_payload=battery, model_payload=model, inference_payload=inference,
                verifier_image_digest="sha256:" + "e" * 64, output_root=output, profile="controller",
            )
            self.assertEqual(result["profile"], "controller")
            self.assertEqual(len(result["tasks"]), 2)
            task = json.loads((output / "battery-trial-controller-harness-analysis/task.json").read_text(encoding="utf-8"))
            verifier = json.loads((output / "battery-trial-controller-harness-analysis/verifier.json").read_text(encoding="utf-8"))
            tools = json.loads((output / "battery-trial-controller-harness-analysis/tools.json").read_text(encoding="utf-8"))
            self.assertEqual((task["profile"], task["journeyId"], task["dataRoute"]), (
                "controller", "inspectable-result-evidence", "local-only",
            ))
            self.assertEqual({item["assertionId"] for item in verifier["checks"]}, {
                "artifact-openable", "evidence-exact-source", "independent-completion", "no-scripted-answer-credit",
            })
            self.assertEqual(tools["brokeredServices"], ["local-model"])
            self.assertFalse(tools["externalEffects"])

    def test_materializer_rejects_a_profile_without_both_partitions_before_creating_output(self):
        battery = json.loads((ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_text(encoding="utf-8"))
        model, inference = contracts()
        partition = lambda task: task.get("partition", task.get("rehearsal", {}).get("partition", "tuning"))
        tuning = [task for task in battery["tasks"] if task.get("profile", "builder") == "builder" and partition(task) == "tuning"]
        held_out = [task for task in battery["tasks"] if task.get("profile", "builder") == "builder" and partition(task) == "held-out"]
        self.assertTrue(tuning and held_out)
        for label, subset in (("tuning-only", tuning), ("held-out-only", held_out)):
            one_sided = {
                "$schema": materializer.BATTERY_SCHEMA, "schemaVersion": 1,
                "operation": "pixel-agent-comparison-task-battery",
                "provenance": "test fixture", "tasks": subset, "boundary": materializer.BATTERY_BOUNDARY,
            }
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "output"
                with self.assertRaisesRegex(materializer.evaluation.OutcomeError, "at least one tuning and one held-out"):
                    materializer.materialize(
                        root=ROOT, battery_payload=payload(one_sided), model_payload=model, inference_payload=inference,
                        verifier_image_digest="sha256:" + "e" * 64, output_root=output, profile="builder",
                    )
                self.assertFalse(output.exists(), f"materializer must fail closed before creating output for {label}")

    def test_formal_researcher_task_uses_an_admitted_offline_corpus_not_a_rehearsal(self):
        battery = json.loads((ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_text(encoding="utf-8"))
        rehearsal = next(item for item in battery["tasks"] if item["taskId"] == "trial-cited-research-rehearsal")
        formal = json.loads(json.dumps(rehearsal))
        formal.update({
            "taskId": "battery-trial-formal-research",
            "source": "curated-workflow",
            "partition": "tuning",
            "trialProof": {
                "productJourneyId": "cited-current-research",
                "trialJourneyIds": ["deep-research-live-cited"],
                "proofClass": "formal-product-path-task",
            },
        })
        formal.pop("rehearsal")
        formal["researchFixture"] = {
            "$schema": materializer.outcome_task.RESEARCH_FIXTURE_SCHEMA,
            "schemaVersion": 1, "operation": "pixel-portal-outcome-research-fixture",
            "observedAt": "2026-08-13T12:05:00Z",
            "sources": [{
                "fixtureSourceId": "primary", "sourceType": "web", "title": "Primary result",
                "snippet": "The pilot completed.", "quality": "primary", "publishedDate": "2026-08-01",
                "retrieval": {"status": "fetched", "content": "The pilot completed after a bounded evaluation."},
            }],
            "authority": {field: False for field in materializer.outcome_task.RESEARCH_FIXTURE_AUTHORITY_FIELDS},
            "boundary": materializer.outcome_task.RESEARCH_FIXTURE_BOUNDARY,
        }
        heldout_researcher = next(
            item for item in battery["tasks"]
            if item.get("profile", "builder") == "researcher"
            and item.get("partition", item.get("rehearsal", {}).get("partition", "tuning")) == "held-out"
        )
        battery["tasks"] = [formal, heldout_researcher]
        model, inference = contracts()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "formal-researcher"
            result = materializer.materialize(
                root=ROOT, battery_payload=payload(battery), model_payload=model, inference_payload=inference,
                verifier_image_digest="sha256:" + "e" * 64, output_root=output, profile="researcher",
            )
            self.assertEqual(result["tasks"][0]["proofClass"], "formal-product-path-task")
            task = json.loads((output / formal["taskId"] / "task.json").read_text(encoding="utf-8"))
            fixture = json.loads((output / formal["taskId"] / "research-fixture.json").read_text(encoding="utf-8"))
            self.assertEqual((task["profile"], task["journeyId"]), ("researcher", "cited-current-research"))
            self.assertEqual(fixture, formal["researchFixture"])

        missing = json.loads(json.dumps(battery))
        missing["tasks"][0].pop("researchFixture")
        with self.assertRaisesRegex(materializer.evaluation.OutcomeError, "exactly one rehearsal or formal research fixture"):
            materializer.validate_battery(payload(missing))

    def test_assistant_profile_materializes_as_the_real_portal_route_not_a_builder_surrogate(self):
        battery = (ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_bytes()
        model, inference = contracts()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "assistant"
            result = materializer.materialize(
                root=ROOT, battery_payload=battery, model_payload=model, inference_payload=inference,
                verifier_image_digest="sha256:" + "e" * 64, output_root=output, profile="assistant",
            )
            self.assertEqual(result["profile"], "assistant")
            self.assertEqual([item["batteryTaskId"] for item in result["tasks"]], [
                "battery-repo-config-precedence", "battery-trial-owner-briefing",
                "battery-trial-conversation-quality", "battery-trial-reasoned-advisory",
            ])
            inventory = result["tasks"][0]
            self.assertEqual((inventory["executionProfile"], inventory["targetProfile"], inventory["profileFidelity"]), (
                "assistant", "assistant", "exact",
            ))
            task = json.loads((output / "battery-repo-config-precedence/task.json").read_text(encoding="utf-8"))
            verifier = json.loads((output / "battery-repo-config-precedence/verifier.json").read_text(encoding="utf-8"))
            tools = json.loads((output / "battery-repo-config-precedence/tools.json").read_text(encoding="utf-8"))
            self.assertEqual((task["profile"], task["journeyId"], task["dataClass"], task["effectBoundary"]), (
                "assistant", "assistant-harness-challenge", "public", "none",
            ))
            self.assertEqual(verifier["journeyId"], "assistant-harness-challenge")
            self.assertIsNotNone(verifier["workspaceVerification"])
            self.assertIn("task", tools["tools"])
            self.assertEqual(tools["brokeredServices"], ["local-model"])
            self.assertTrue(all(value is False for value in task["authority"].values()))


if __name__ == "__main__":
    unittest.main()
