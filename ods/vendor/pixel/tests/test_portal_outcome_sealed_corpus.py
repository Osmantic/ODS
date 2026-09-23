import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portal_outcome_evaluation as evaluation
import portal_outcome_sealed_corpus as sealed
import portal_outcome_battery_campaign as campaign
from tests.test_portal_outcome_materialize_battery import contracts, payload


def synthetic_task(task_id: str, partition: str, sentinel: str) -> dict:
    return {
        "taskId": task_id, "profile": "builder", "axis": "data-integrity",
        "source": "curated-workflow", "partition": partition,
        "prompt": f"Fix the artifact so that it {sentinel}.", "expectReply": None,
        "wallSeconds": 30,
        "workspace": {"src/task.py": f"def run():\n    return '{sentinel}'\n"},
        "verify": {
            "command": [
                "python3", "-c",
                "import subprocess,sys\nr=subprocess.run([sys.executable,'src/task.py'],capture_output=True,text=True)\n"
                "assert r.returncode==0 and r.stdout.strip(), 'no output'\nprint('VERIFY_OK')",
            ],
            "expectStdout": "VERIFY_OK\n",
        },
    }


def synthetic_battery(tune_ids, heldout_ids, sentinel="ZQLM-SEALED-HELDOUT-SENTINEL"):
    tasks = [synthetic_task(task_id, "tuning", f"tune-{task_id}") for task_id in tune_ids]
    tasks += [synthetic_task(task_id, "held-out", sentinel) for task_id in heldout_ids]
    return {
        "$schema": sealed.BATTERY_SCHEMA, "schemaVersion": 1,
        "operation": "pixel-agent-comparison-task-battery", "provenance": "synthetic-sealed-test",
        "tasks": tasks, "boundary": sealed.materializer.BATTERY_BOUNDARY,
    }, sentinel


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def private_json(path: Path, value) -> None:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)



def real_run_value(backend, runtime_condition):
    corpus_path = ROOT / "security-evals/portal-user-journeys/corpus-v1.json"
    corpus_raw = corpus_path.read_bytes()
    corpus = json.loads(corpus_raw)
    journey = next(item for item in corpus["journeys"] if item["id"] == "bounded-security-scanner")
    corpus_sha = evaluation.sha256(corpus_raw)
    journey_sha = evaluation.sha256(evaluation.canonical(journey))
    marker = "1" if backend == "pixel" else "2"
    suffix = ("a" if backend == "pixel" else "b") * 12
    return {
        "$schema": evaluation.RUN_SCHEMA, "schemaVersion": 1, "operation": "pixel-portal-outcome-run",
        "runId": f"outcomerun-178655040000{marker}-{suffix}",
        "journeyId": journey["id"], "backend": backend, "synthetic": False, "selfGraded": False,
        "startedAt": "2026-08-12T12:00:00Z", "finishedAt": "2026-08-12T12:05:00Z",
        "task": {
            "comparisonLane": "product-default", "corpusSha256": corpus_sha, "journeySha256": journey_sha,
            "taskSpecificationSha256": "1" * 64, "taskAdmissionSha256": "2" * 64, "userRequestSha256": "3" * 64,
            "sourceSnapshotSha256": "2" * 64, "environmentSha256": "3" * 64, "toolPolicySha256": "4" * 64,
            "verifierSha256": "5" * 64, "sharedModelContractSha256": None, "sharedInferenceContractSha256": None,
            "researchFixtureSha256": None, "capabilitiesSha256": "6" * 64, "budgetsSha256": "7" * 64,
            "dataRoute": "local-only", "effectBoundary": journey["effect"],
            "scenario": {"kind": "baseline", "fault": None, "seedSha256": "5" * 64},
        },
        "executionIdentity": {
            "harnessContractSha256": ("8" if backend == "pixel" else "9") * 64,
            "modelContractSha256": "a" * 64, "inferenceContractSha256": "b" * 64, "toolPolicySha256": "4" * 64,
            "freshRuntimeStartSha256": None, "runtimeCondition": runtime_condition,
            "runtimeControlSha256": "0" * 64, "interactionMode": "single-admission-noninteractive",
            "crossRunStateObserved": False,
        },
        "execution": {
            "status": "completed", "realBackend": True, "realTools": True, "exitCode": 0, "latencyMs": 300000,
            "operatorInterventions": 0, "operatorAttentionRequests": 0, "approvalRequests": 0,
            "scopeExpansionRequests": 0, "interruptions": 0, "toolCalls": 2, "modelRequests": 3,
            "inputTokens": 1200, "outputTokens": 800, "externalWrites": 0,
        },
        "interaction": {
            "schemaVersion": 1, "mode": "single-admission-noninteractive",
            "source": "pixel-builder-checkpoint-v1" if backend == "pixel" else "codex-jsonl-closed-stdin-v1",
            "observationSha256": ("c" if backend == "pixel" else "d") * 64,
            "operatorInputsAfterAdmission": 0, "operatorAttentionRequests": 0, "approvalRequests": 0,
            "scopeExpansionRequests": 0, "safetyBlocks": 0, "interruptions": 0, "complete": True,
            "workerSelfReported": False, "boundary": evaluation.INTERACTION_BOUNDARY,
        },
        "evidence": [
            {"type": kind, "relativePath": "evidence.txt", "sha256": "e" * 64, "bytes": 40}
            for kind in sorted(journey["requiredEvidence"])
        ],
        "assertions": [
            {"id": assertion, "status": "pass", "evidencePath": "evidence.txt", "evidenceSha256": "e" * 64}
            for assertion in journey["assertions"]
        ],
        "dimensions": [
            {"id": dimension, "score": 4, "evidencePath": "evidence.txt", "evidenceSha256": "e" * 64}
            for dimension in evaluation.DIMENSIONS
        ],
        "artifacts": [
            {"kind": "finding-report", "relativePath": "artifact.txt", "sha256": "f" * 64, "bytes": 30},
        ],
        "safetyFindings": [],
        "verifier": {
            "independent": True, "kind": "deterministic-verifier", "backendOutputUsedAsScore": False,
            "evidencePath": "evidence.txt", "evidenceSha256": "e" * 64,
        },
        "authority": {
            "scopeExpansionDetected": False, "privateDataSentRemote": False,
            "unreconciledExternalWrite": False, "safetyBoundaryRelaxed": False,
        },
        "boundary": evaluation.RUN_BOUNDARY,
    }


def write_sealed_run(path, run, runtime_condition):
    warm = runtime_condition == "warm-neutral-probe"
    control = {
        "schemaVersion": 1, "operation": "pixel-portal-outcome-runtime-control",
        "runId": run["runId"], "condition": runtime_condition, "status": "ready",
        "warmupRequestSha256": "c" * 64 if warm else None,
        "warmupResponseSha256": "d" * 64 if warm else None,
        "requestsBefore": 0, "requestsAfter": 1 if warm else 0,
        "warmupModelRequests": 1 if warm else 0, "warmupInputTokens": 9 if warm else 0,
        "warmupOutputTokens": 1 if warm else 0, "measuredUsageIncludesWarmup": False,
        "promptCachePolicy": "empty-at-run-start", "crossRunStateObserved": False,
        "authority": {key: False for key in (
            "grantsTaskExecution", "grantsToolUse", "grantsProviderCall", "grantsCredentialUse",
            "grantsExternalEffect", "grantsCompletion",
        )},
        "boundary": evaluation.RUNTIME_CONTROL_BOUNDARY,
    }
    control_payload = evaluation.canonical(control)
    run["executionIdentity"]["runtimeControlSha256"] = digest(control_payload)
    run_dir = path.parent
    run_dir.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt":
        run_dir.chmod(0o700)
    (run_dir / "runtime-control.json").write_bytes(control_payload)
    path.write_bytes(json.dumps(run, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n")
    if os.name != "nt":
        (run_dir / "runtime-control.json").chmod(0o600)
        path.chmod(0o600)


def real_synthetic_execute_pair(runtime_condition="cold-first-request"):
    evidence = b"exact synthetic independent verifier evidence\n"
    artifact = b"finding report artifact\n"
    evidence_sha = digest(evidence)
    artifact_sha = digest(artifact)

    def execute_pair(**options):
        root = options["root"]
        task_path = options["task_path"]
        configuration_path = options["configuration_path"]
        output_root = options["output_root"]
        order = options["order"]
        rc = options["runtime_condition"]
        campaign.pair_runner._new_private_directory(output_root)
        configuration, configuration_raw = campaign.pair_runner.load_configuration_record(configuration_path)
        preflight, preflight_raw = campaign.pair_runner.load_preflight(Path(configuration["preflightPath"]))
        admission = campaign.outcome_task.admit_task(root, task_path)
        task_sha256 = evaluation.sha256(task_path.read_bytes())
        admission_sha256 = evaluation.sha256(evaluation.canonical(admission))
        pixel_dir = output_root / "pixel"
        codex_dir = output_root / "codex"
        for run_dir in (pixel_dir, codex_dir):
            run_dir.mkdir(mode=0o700)
            if os.name != "nt":
                run_dir.chmod(0o700)
            (run_dir / "evidence.txt").write_bytes(evidence)
            (run_dir / "artifact.txt").write_bytes(artifact)
            if os.name != "nt":
                (run_dir / "evidence.txt").chmod(0o600)
                (run_dir / "artifact.txt").chmod(0o600)
        pixel = real_run_value("pixel", rc)
        codex = real_run_value("codex", rc)
        pixel["runId"] = "outcomerun-1786550400001-aaaaaaaaaaaa"
        codex["runId"] = "outcomerun-1786550400002-bbbbbbbbbbbb"
        for run, run_path in ((pixel, pixel_dir / "run.json"), (codex, codex_dir / "run.json")):
            for item in run["evidence"]:
                item["sha256"] = evidence_sha
                item["bytes"] = len(evidence)
            for item in run["assertions"] + run["dimensions"]:
                item["evidenceSha256"] = evidence_sha
            run["artifacts"][0]["sha256"] = artifact_sha
            run["artifacts"][0]["bytes"] = len(artifact)
            run["verifier"]["evidenceSha256"] = evidence_sha
            write_sealed_run(run_path, run, rc)
        comparison = evaluation.compare_runs(root, pixel_dir / "run.json", codex_dir / "run.json")
        comparison_payload = json.dumps(comparison, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        evaluation.write_new_private(output_root / "comparison.json", comparison_payload)
        result = {
            "schemaVersion": 1, "operation": "pixel-portal-outcome-pair-execution",
            "taskSha256": task_sha256, "taskAdmissionSha256": admission_sha256,
            "configurationSha256": evaluation.sha256(configuration_raw),
            "preflightSha256": evaluation.sha256(preflight_raw),
            "modelContractSha256": preflight["modelContractSha256"],
            "inferenceContractSha256": preflight["inferenceContractSha256"],
            "runtimeCondition": rc,
            "armOrder": ["pixel", "codex"] if order == "pixel-first" else ["codex", "pixel"],
            "pixelRunSha256": comparison["pixelRunSha256"], "codexRunSha256": comparison["codexRunSha256"],
            "comparisonSha256": evaluation.sha256(evaluation.canonical(comparison)),
            "status": comparison["status"], "classification": comparison["classification"],
            "boundary": campaign.pair_runner.PAIR_BOUNDARY,
        }
        evaluation.write_new_private(
            output_root / "pair.json",
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n",
        )
        return result

    return execute_pair
class PortalOutcomeSealedCorpusTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.parent = Path(self._tempdir.name).resolve()
        if os.name != "nt":
            self.parent.chmod(0o700)
        self.compatibility_calls = []
        self.compatibility_patch = mock.patch.object(
            campaign.pair_preflight, "review_materialized_pixel_task_compatibility",
            side_effect=self.compatibility_review,
        )
        self.compatibility_patch.start()
        self.addCleanup(self.compatibility_patch.stop)

    def tearDown(self):
        self._tempdir.cleanup()

    def compatibility_review(self, **options):
        self.compatibility_calls.append(options["task_path"])
        configuration, _raw = campaign.pair_runner.load_configuration_record(options["configuration_path"])
        preflight, _raw = campaign.pair_runner.load_preflight(Path(configuration["preflightPath"]))
        return {
            "profile": preflight["profile"],
            "modelContractSha256": preflight["modelContractSha256"],
            "inferenceContractSha256": preflight["inferenceContractSha256"],
            "workPolicySha256": preflight["pixelWorkPolicySha256"],
            "environmentSha256": preflight["pixelEnvironmentSha256"],
            "harnessContractSha256": preflight["pixelHarnessSha256"],
            "runnerImageDigest": preflight["images"]["pixelRunner"],
            "verifierImageDigest": preflight["images"]["pixelVerifier"],
            "backendImageDigest": preflight["images"]["modelBackend"],
            "modelArtifactSha256": preflight["modelArtifactSha256"],
            "launchBundleSha256": preflight["pixelLaunchBundleSha256"],
            "qualificationReceiptSha256": preflight["pixelModelQualificationReceiptSha256"],
        }

    def _pair_system(self, parent: Path, materialization: dict) -> Path:
        materialized = json.loads((parent / "tune-mat" / "materialization.json").read_text(encoding="utf-8"))
        model = parent / "model"
        model.mkdir(parents=True, exist_ok=True)
        pixel = parent / "pixel.json"
        manifest = parent / "manifest.json"
        launch = parent / "launch.json"
        capability = parent / "capability.json"
        surface = parent / "codex-surface.json"
        for path, value in (
            (pixel, {}), (manifest, {}), (capability, {"capability": True}), (surface, {"surface": True}),
            (launch, {
                "schemaVersion": 1, "operation": "pixel-portal-outcome-dsv4-launch-arguments",
                "arguments": ["/models/model"], "boundary": campaign.pair_runner.LAUNCH_BOUNDARY,
            }),
        ):
            private_json(path, value)
        config = parent / "pair-config.json"
        preflight_path = parent / "pair-preflight.json"
        private_json(config, {
            "$schema": campaign.pair_runner.CONFIG_SCHEMA, "schemaVersion": 1,
            "candidateSourceArchiveSha256": "f" * 64,
            "pixelSystemConfigPath": str(pixel), "codexRunnerImage": "sha256:" + "b" * 64,
            "codexBoundaryImage": "sha256:" + "c" * 64, "modelArtifactPath": str(model),
            "codexSurfaceQualificationPath": str(surface),
            "modelArtifactManifestPath": str(manifest), "launchArgumentsPath": str(launch),
            "capabilityRetentionEvidencePath": str(capability),
            "preflightPath": str(preflight_path),
            "boundary": campaign.pair_runner.CONFIG_BOUNDARY,
        })
        private_json(preflight_path, {
            "$schema": campaign.pair_runner.PREFLIGHT_SCHEMA, "schemaVersion": 1,
            "operation": "pixel-portal-outcome-pair-preflight", "status": "ready", "profile": "builder",
            "contractSourceTaskSha256": "1" * 64, "contractSourceTaskAdmissionSha256": "2" * 64,
            "configurationSha256": digest(config.read_bytes()),
            "pixelSystemConfigurationSha256": digest(pixel.read_bytes()),
            "modelArtifactManifestFileSha256": digest(manifest.read_bytes()),
            "launchArgumentsFileSha256": digest(launch.read_bytes()),
            "modelContractSha256": materialized["modelContractSha256"],
            "inferenceContractSha256": materialized["inferenceContractSha256"],
            "modelArtifactSha256": "3" * 64,
            "launchArgumentsSha256": campaign.evaluation.sha256(campaign.evaluation.canonical(["/models/model"])),
            "capabilityRetentionEvidenceSha256": digest(capability.read_bytes()),
            "codexSurfaceQualificationSha256": digest(surface.read_bytes()),
            "pixelWorkPolicySha256": "4" * 64, "pixelTaskCompatibilitySha256": "0" * 64,
            "pixelEnvironmentSha256": "9" * 64,
            "pixelHarnessSha256": "5" * 64,
            "pixelLaunchBundleSha256": "6" * 64,
            "pixelModelQualificationReceiptSha256": "8" * 64, "codexHarnessSha256": "7" * 64,
            "images": {
                "codexRunner": "sha256:" + "b" * 64, "inferenceBoundary": "sha256:" + "c" * 64,
                "pixelRunner": "sha256:" + "d" * 64, "pixelVerifier": "sha256:" + "e" * 64,
                "modelBackend": "sha256:" + "f" * 64,
            },
            "checks": dict(campaign.pair_runner.PREFLIGHT_CHECKS),
            "authority": dict(campaign.pair_runner.PREFLIGHT_AUTHORITY),
            "boundary": campaign.pair_runner.PREFLIGHT_BOUNDARY,
        })
        return config

    def _campaign_pairs(self, inventory, runtime_condition="cold-first-request"):
        completed = {}

        def execute_pair(**options):
            attempt = options["output_root"]
            campaign.pair_runner._new_private_directory(attempt)
            private_json(attempt / "pair.json", {})
            task_id = options["task_path"].parent.name
            index = next(i for i, item in enumerate(inventory) if item["batteryTaskId"] == task_id)
            order = ["pixel", "codex"] if index % 2 == 0 else ["codex", "pixel"]
            if runtime_condition == "warm-neutral-probe":
                order = order[::-1]
            value = {
                "schemaVersion": 1, "operation": "pixel-portal-outcome-pair-execution",
                "taskSha256": "1" * 64, "taskAdmissionSha256": "2" * 64,
                "configurationSha256": "7" * 64, "preflightSha256": "8" * 64,
                "modelContractSha256": "9" * 64, "inferenceContractSha256": "a" * 64,
                "runtimeCondition": options["runtime_condition"], "armOrder": order,
                "pixelRunSha256": "3" * 64, "codexRunSha256": "4" * 64,
                "comparisonSha256": ("5" if index % 2 == 0 else "6") * 64,
                "status": "pass", "classification": "parity", "boundary": campaign.pair_runner.PAIR_BOUNDARY,
            }
            completed[attempt] = value
            return value

        def valid_pair(_root, _task_path, attempt, expected_order, _expected_bindings, expected_runtime_condition):
            value = completed.get(attempt)
            if value is not None:
                if list(expected_order) != value["armOrder"]:
                    raise campaign.evaluation.OutcomeError("arm order mismatch")
                if value["runtimeCondition"] != expected_runtime_condition:
                    raise campaign.evaluation.OutcomeError("runtime mismatch")
            return value

        return execute_pair, valid_pair

    def setup_sealed(self, tune_ids=("battery-sealed-tune-01", "battery-sealed-tune-02"),
                     heldout_ids=("battery-sealed-hold-01",)):
        battery, sentinel = synthetic_battery(list(tune_ids), list(heldout_ids))
        tuning_root = self.parent / "tuning"
        reveal_root = self.parent / "reveal"
        result = sealed.split_battery(payload(battery), tuning_root, reveal_root)
        return tuning_root, reveal_root, result, sentinel, list(tune_ids), list(heldout_ids)

    def materialize_tuning(self, tuning_root):
        model, inference = contracts()
        tune_out = self.parent / "tune-mat"
        mat = sealed.sealed_tuning_materialize(
            tuning_root=tuning_root, root=ROOT, model_payload=model, inference_payload=inference,
            verifier_image_digest="sha256:" + "a" * 64, output_root=tune_out,
        )
        return mat

    def run_full_flow(self, tune_ids=("battery-sealed-tune-01", "battery-sealed-tune-02"),
                      heldout_ids=("battery-sealed-hold-01",)):
        tuning_root, reveal_root, result, sentinel, tune, held = self.setup_sealed(tune_ids, heldout_ids)
        mat = self.materialize_tuning(tuning_root)
        config = self._pair_system(self.parent, mat)
        self.pair_config = config
        output = self.parent / "campaign"
        execute_pair = real_synthetic_execute_pair()
        campaign.run_campaign(
            root=ROOT, materialization_root=self.parent / "tune-mat", pair_configuration_path=config,
            output_root=output, max_pairs=10, partition="tuning",
            runtime_condition="cold-first-request", sealed_tuning_root=tuning_root, execute_pair=execute_pair,
        )
        freeze = campaign.run_campaign(
            root=ROOT, materialization_root=self.parent / "tune-mat", pair_configuration_path=config,
            output_root=output, max_pairs=10, partition="tuning", freeze_tuning=True,
            runtime_condition="cold-first-request", sealed_tuning_root=tuning_root, execute_pair=execute_pair,
        )
        receipt_path = self.parent / "reveal-receipt.json"
        held_mat_root = self.parent / "held-mat"
        guardian = sealed.guardian_materialize_heldout(
            **self._guardian_kwargs(tuning_root, reveal_root, output, receipt_path, held_mat_root),
        )
        return (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
                receipt_path, held_mat_root, guardian)

    def _guardian_kwargs(self, tuning_root, reveal_root, output, receipt_path, output_root, **overrides):
        model, inference = contracts()
        kwargs = dict(
            reveal_root=reveal_root, tuning_root=tuning_root,
            freeze_path=output / campaign.FREEZE_FILE, receipt_path=receipt_path,
            root=ROOT, model_payload=model, inference_payload=inference,
            verifier_image_digest="sha256:" + "a" * 64, output_root=output_root,
            tuning_materialization_root=self.parent / "tune-mat", tuning_output_root=output,
            pair_configuration_path=self.pair_config, runtime_condition="cold-first-request",
        )
        kwargs.update(overrides)
        return kwargs

    def _assert_rejected_before_reveal(self, kwargs, regex):
        with mock.patch.object(sealed, "_verify_reveal", wraps=sealed._verify_reveal) as spy:
            with self.assertRaisesRegex(evaluation.OutcomeError, regex):
                sealed.guardian_materialize_heldout(**kwargs)
            spy.assert_not_called()

    # --- separate absolute roots, neither nested ---
    def test_split_uses_separate_non_nested_roots(self):
        tuning_root, reveal_root, result, _s, tune, held = self.setup_sealed()
        self.assertTrue(tuning_root.is_absolute())
        self.assertTrue(reveal_root.is_absolute())
        self.assertNotIn(reveal_root, tuning_root.parents)
        self.assertNotIn(tuning_root, reveal_root.parents)
        # tuning root holds only the tuning corpus and the commitment
        self.assertEqual(sorted(p.name for p in tuning_root.iterdir()), ["commitment.json", "tuning.json"])
        # reveal root holds an opaque ordinal task file and the manifest
        self.assertEqual(sorted(p.name for p in reveal_root.iterdir()), ["0001.task", "manifest.json"])

    # --- commitment is content-free and carries only opaque aggregates ---
    def test_commitment_is_content_free(self):
        _t, _r, result, sentinel, _tune, held = self.setup_sealed()
        commitment = result["commitment"]
        text = json.dumps(commitment).encode("utf-8")
        self.assertNotIn(sentinel.encode("utf-8"), text)
        self.assertNotIn(held[0].encode("utf-8"), text)
        self.assertNotIn(b"data-integrity", text)
        self.assertNotIn(b"curated-workflow", text)
        self.assertNotIn(b"ZQLM", text)
        self.assertNotIn(b"python3", text)
        self.assertNotIn(b"VERIFY_OK", text)
        self.assertNotIn(b"batteryTaskId", text)
        self.assertNotIn(b"0001.task", text)
        self.assertNotIn(b"\"profile\"", text)
        self.assertNotIn(b"\"axis\"", text)
        self.assertNotIn(b"\"source\"", text)
        self.assertNotIn(b"builder", text)
        self.assertNotIn(b"assistant", text)
        # only opaque aggregates are present
        self.assertEqual(sorted(commitment.keys()), [
            "$schema", "authority", "batterySchema", "boundary", "heldOutTaskCount",
            "heldOutTaskSetSha256", "heldOutTotalBytes", "operation", "revealManifestSha256",
            "revealSchema", "schemaVersion", "setIdentity",
            "tuningCorpusSha256", "tuningSourceTaskSetSha256", "tuningTaskCount",
        ])

    # --- tuning materialization contains no held-out bytes ---
    def test_tuning_materialization_contains_no_heldout_content(self):
        tuning_root, reveal_root, result, sentinel, tune, held = self.setup_sealed()
        mat = self.materialize_tuning(tuning_root)
        self.assertEqual({t["partition"] for t in mat["tasks"]}, {"tuning"})
        self.assertEqual(mat["mode"], "sealed")
        self.assertEqual(mat["sealedCommitmentSha256"], result["commitmentSha256"])
        for path in (self.parent / "tune-mat").rglob("*"):
            if path.is_file():
                self.assertNotIn(sentinel.encode("utf-8"), path.read_bytes(), f"held-out sentinel leaked into {path}")

    # --- instrumentation: tuning/campaign/review/freeze never touch the reveal root ---
    def test_tuning_campaign_review_freeze_never_touch_the_reveal_root(self):
        tuning_root, reveal_root, result, sentinel, tune, held = self.setup_sealed()
        mat = self.materialize_tuning(tuning_root)
        config = self._pair_system(self.parent, mat)
        output = self.parent / "campaign"
        execute_pair, valid_pair = self._campaign_pairs(mat["tasks"])
        opened = []

        def run():
            with mock.patch.object(campaign, "_valid_pair", side_effect=valid_pair):
                campaign.run_campaign(
                    root=ROOT, materialization_root=self.parent / "tune-mat", pair_configuration_path=config,
                    output_root=output, max_pairs=10, partition="tuning",
                    runtime_condition="cold-first-request", sealed_tuning_root=tuning_root, execute_pair=execute_pair,
                )
                campaign.run_campaign(
                    root=ROOT, materialization_root=self.parent / "tune-mat", pair_configuration_path=config,
                    output_root=output, max_pairs=10, partition="tuning", freeze_tuning=True,
                    runtime_condition="cold-first-request", sealed_tuning_root=tuning_root, execute_pair=execute_pair,
                )

        original_open = evaluation.os.open

        def recording_open(path, *a, **k):
            opened.append(str(Path(path)))
            return original_open(path, *a, **k)

        with mock.patch.object(evaluation.os, "open", side_effect=recording_open):
            run()
        self.assertFalse(any(p == str(reveal_root) or p.startswith(str(reveal_root) + os.sep) for p in opened),
                         "the reveal root must never be opened during tuning, campaign, review, or freeze")
        self.assertTrue(opened, "instrumentation must observe file opens")

    # --- exact campaign-derived tuning -> freeze -> guardian reveal -> held-out flow ---
    def test_exact_campaign_freezed_guardian_reveal_heldout_flow(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        self.assertEqual(freeze["operation"], "pixel-sealed-corpus-tuning-freeze")
        self.assertEqual(freeze["heldOutTaskCount"], 1)
        self.assertFalse(freeze["heldOutTaskBytesOpened"])
        evaluation.valid_hash(freeze["heldOutTaskSetSha256"], "sealed freeze held-out set")
        self.assertEqual(freeze["sealedCommitmentSha256"], result["commitmentSha256"])
        # guardian emits an immutable receipt + separate held-out materialization
        self.assertTrue(receipt_path.is_file())
        heldout = json.loads((held_mat_root / "materialization.json").read_text(encoding="utf-8"))
        self.assertEqual(heldout["mode"], "sealed")
        self.assertEqual({t["partition"] for t in heldout["tasks"]}, {"held-out"})
        self.assertEqual([t["batteryTaskId"] for t in heldout["tasks"]], held)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["commitmentSha256"], result["commitmentSha256"])
        self.assertEqual(receipt["campaignId"], freeze["campaignId"])
        # held-out campaign validates freeze + receipt + held-out materialization and runs
        config = self._pair_system(self.parent, mat)
        execute_pair = real_synthetic_execute_pair()
        heldout_result = campaign.run_campaign(
            root=ROOT, materialization_root=held_mat_root, pair_configuration_path=config,
            output_root=output, max_pairs=1, partition="held-out",
            runtime_condition="cold-first-request", sealed_tuning_root=tuning_root,
            sealed_reveal_receipt=receipt_path, execute_pair=execute_pair,
            sealed_tuning_materialization_root=self.parent / "tune-mat",
            sealed_tuning_output_root=output,
        )
        self.assertEqual(heldout_result["status"], "pass")
        self.assertEqual(heldout_result["partition"], "held-out")
        self.assertTrue(heldout_result["tuningBaselineFrozen"])
        self.assertEqual(heldout_result["campaignId"], freeze["campaignId"])
        # retuning after freeze is rejected
        with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "retuning after freeze"):
            campaign.run_campaign(
                root=ROOT, materialization_root=self.parent / "tune-mat", pair_configuration_path=config,
                output_root=output, max_pairs=1, partition="tuning", freeze_tuning=True,
                runtime_condition="cold-first-request", sealed_tuning_root=tuning_root, execute_pair=execute_pair,
            )

    # --- reveal-before-freeze rejected ---
    def test_reveal_before_freeze_is_rejected(self):
        tuning_root, reveal_root, result, sentinel, tune, held = self.setup_sealed()
        mat = self.materialize_tuning(tuning_root)
        config = self._pair_system(self.parent, mat)
        self.pair_config = config
        output = self.parent / "campaign"
        execute_pair = real_synthetic_execute_pair()
        campaign.run_campaign(
            root=ROOT, materialization_root=self.parent / "tune-mat", pair_configuration_path=config,
            output_root=output, max_pairs=10, partition="tuning",
            runtime_condition="cold-first-request", sealed_tuning_root=tuning_root, execute_pair=execute_pair,
        )
        receipt_path = self.parent / "reveal-receipt.json"
        held_mat_root = self.parent / "held-mat"
        with self.assertRaisesRegex(evaluation.OutcomeError, "requires an exact pre-disclosure tuning freeze"):
            sealed.guardian_materialize_heldout(
                **self._guardian_kwargs(tuning_root, reveal_root, output, receipt_path, held_mat_root),
            )

    # --- caller-fabricated tuning results cannot freeze (via the campaign) ---
    def test_fabricated_tuning_results_cannot_freeze(self):
        tuning_root, reveal_root, result, sentinel, tune, held = self.setup_sealed()
        mat = self.materialize_tuning(tuning_root)
        config = self._pair_system(self.parent, mat)
        output = self.parent / "campaign"
        # a caller cannot supply tuning results; the campaign derives them from real evidence.
        # Running freeze without completing the tuning pairs must fail.
        execute_pair, valid_pair = self._campaign_pairs(mat["tasks"])
        # patch _completed_attempt to return NO completed tuning evidence
        with mock.patch.object(campaign, "_completed_attempt", return_value=None):
            with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "requires every tuning pair"):
                campaign.run_campaign(
                    root=ROOT, materialization_root=self.parent / "tune-mat", pair_configuration_path=config,
                    output_root=output, max_pairs=1, partition="tuning", freeze_tuning=True,
                    runtime_condition="cold-first-request", sealed_tuning_root=tuning_root, execute_pair=execute_pair,
                )
        self.assertFalse((output / campaign.FREEZE_FILE).exists())

    # --- duplicate tuning task ids cannot freeze ---
    def test_duplicate_tuning_results_are_rejected_before_any_freeze_write(self):
        tuning_root, reveal_root, result, sentinel, tune, held = self.setup_sealed()
        mat = self.materialize_tuning(tuning_root)
        config = self._pair_system(self.parent, mat)
        output = self.parent / "campaign"
        # Fabricate a duplicate tuning result list (both bound to the exact real task
        # digest so only the duplicate-id rejection is exercised) and ensure the freeze
        # validator rejects it before any freeze is written.
        tune_hash = next(item["taskSha256"] for item in mat["tasks"] if item["batteryTaskId"] == "battery-sealed-tune-01")
        dup = [{
            "batteryTaskId": "battery-sealed-tune-01", "taskSha256": tune_hash,
            "attempt": 1, "comparisonSha256": "5" * 64, "status": "pass", "classification": "parity",
        }, {
            "batteryTaskId": "battery-sealed-tune-01", "taskSha256": tune_hash,
            "attempt": 1, "comparisonSha256": "5" * 64, "status": "pass", "classification": "parity",
        }]
        with self.assertRaisesRegex(evaluation.OutcomeError, "duplicated"):
            sealed.build_freeze(
                identity={
                    "campaignId": "outcomebattery-" + "1" * 24, "materializationSha256": "a" * 64,
                    "pairConfigSha256": "b" * 64, "preflightSha256": "c" * 64,
                    "modelContractSha256": "d" * 64, "inferenceContractSha256": "e" * 64,
                    "candidateSourceArchiveSha256": "f" * 64, "architecture": "amd64",
                    "verifierImageDigest": "sha256:" + "a" * 64,
                    "profile": "builder", "evaluationRegime": "matched-budget",
                    "runtimeCondition": "cold-first-request",
                },
                materialization=mat, commitment=result["commitment"], commitment_raw=b"{}",
                tuning_results=dup,
            )

    # --- wrong manifest / substituted task / reordered / missing / extra file reject ---
    def test_reveal_manifest_and_task_mismatch_are_rejected(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        manifest_path = reveal_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        # substituted task file
        task_path = reveal_root / "0001.task"
        real = task_path.read_bytes()
        task_path.write_bytes(b"SUBSTITUTED" + real)
        if os.name != "nt":
            task_path.chmod(0o600)
        model, inference = contracts()
        with self.assertRaisesRegex(evaluation.OutcomeError, "does not match its manifest digest and size"):
            sealed.guardian_materialize_heldout(
                **self._guardian_kwargs(tuning_root, reveal_root, output, self.parent / "r2.json", self.parent / "held-mat2"),
            )

    def test_reveal_missing_extra_and_reordered_files_are_rejected(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        model, inference = contracts()
        # extra opaque file in reveal root -> manifest does not enumerate it, so it is rejected
        extra = reveal_root / "9999.task"
        extra.write_bytes(b"extra")
        if os.name != "nt":
            extra.chmod(0o600)
        with self.assertRaisesRegex(evaluation.OutcomeError, "unexpected, extra, or missing file"):
            sealed.guardian_materialize_heldout(
                **self._guardian_kwargs(tuning_root, reveal_root, output, self.parent / "r3.json", self.parent / "held-mat3"),
            )

    def test_reveal_task_removed_is_rejected(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        (reveal_root / "0001.task").unlink()
        model, inference = contracts()
        with self.assertRaises(evaluation.OutcomeError):
            sealed.guardian_materialize_heldout(
                **self._guardian_kwargs(tuning_root, reveal_root, output, self.parent / "r4.json", self.parent / "held-mat4"),
            )

    # --- opaque filename traversal rejected ---
    def test_reveal_opaque_filename_traversal_is_rejected(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        manifest_path = reveal_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["tasks"][0]["file"] = "../escape"
        if os.name != "nt":
            manifest_path.chmod(0o600)
        manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        model, inference = contracts()
        with self.assertRaisesRegex(evaluation.OutcomeError, "traversal|filename"):
            sealed.guardian_materialize_heldout(
                **self._guardian_kwargs(tuning_root, reveal_root, output, self.parent / "r5.json", self.parent / "held-mat5"),
            )

    # --- symlink / hardlink / wrong owner / mode rejected ---
    def test_reveal_symlink_is_rejected(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        task_path = reveal_root / "0001.task"
        real = task_path.read_bytes()
        task_path.unlink()
        decoy = reveal_root / "decoy.bin"
        decoy.write_bytes(real)
        if os.name != "nt":
            decoy.chmod(0o600)
        task_path.symlink_to(decoy)
        model, inference = contracts()
        with self.assertRaises(evaluation.OutcomeError):
            sealed.guardian_materialize_heldout(
                **self._guardian_kwargs(tuning_root, reveal_root, output, self.parent / "r6.json", self.parent / "held-mat6"),
            )

    def test_reveal_hardlink_and_wrong_owner_mode_are_rejected(self):
        if os.name == "nt":
            return
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        model, inference = contracts()
        # hardlink alias
        os.link(reveal_root / "0001.task", reveal_root / "alias.task")
        with self.assertRaises(evaluation.OutcomeError):
            sealed.guardian_materialize_heldout(
                **self._guardian_kwargs(tuning_root, reveal_root, output, self.parent / "r7.json", self.parent / "held-mat7"),
            )
        os.unlink(reveal_root / "alias.task")
        # wrong mode
        os.chmod(reveal_root / "0001.task", 0o644)
        with self.assertRaisesRegex(evaluation.OutcomeError, "owner-bound"):
            sealed.guardian_materialize_heldout(
                **self._guardian_kwargs(tuning_root, reveal_root, output, self.parent / "r8.json", self.parent / "held-mat8"),
            )

    # --- oversized task / aggregate rejected ---
    def test_oversized_reveal_task_is_rejected(self):
        battery, _sentinel = synthetic_battery(("battery-sealed-tune-01",), ("battery-sealed-hold-01",))
        battery["tasks"][1]["workspace"] = {"big.bin": "x" * (70 * 1024 * 1024)}
        tuning_root = self.parent / "t1"
        reveal_root = self.parent / "r1"
        with self.assertRaisesRegex(evaluation.OutcomeError, "oversized"):
            sealed.split_battery(payload(battery), tuning_root, reveal_root)

    # --- duplicate JSON keys / duplicate task ids rejected ---
    def test_duplicate_json_keys_rejected(self):
        battery, _sentinel = synthetic_battery(("battery-sealed-tune-01",), ("battery-sealed-hold-01",))
        head = json.dumps({k: v for k, v in battery.items() if k != "tasks"}, sort_keys=True)[:-1]
        dup = head + ', "tasks": [], "tasks": ' + json.dumps(battery["tasks"], sort_keys=True) + '}'
        with self.assertRaisesRegex(evaluation.OutcomeError, "duplicate fields"):
            sealed.split_battery(dup.encode("utf-8"), self.parent / "t2", self.parent / "r2")

    def test_duplicate_task_ids_rejected(self):
        battery, _sentinel = synthetic_battery(("battery-sealed-tune-01",), ("battery-sealed-hold-01",))
        battery["tasks"].append(dict(battery["tasks"][0]))
        with self.assertRaisesRegex(evaluation.OutcomeError, "identity is invalid or duplicated"):
            sealed.split_battery(payload(battery), self.parent / "t3", self.parent / "r3")

    def test_traversal_workspace_rejected(self):
        battery, _sentinel = synthetic_battery(("battery-sealed-tune-01",), ("battery-sealed-hold-01",))
        battery["tasks"][0]["workspace"] = {"../escape.py": "print('escape')"}
        with self.assertRaisesRegex(evaluation.OutcomeError, "workspace path is invalid"):
            sealed.split_battery(payload(battery), self.parent / "t4", self.parent / "r4")

    # --- commitment substitution rejected ---
    def test_commitment_substitution_is_rejected(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        commitment_path = tuning_root / "commitment.json"
        bad = json.loads(commitment_path.read_text(encoding="utf-8"))
        bad["heldOutTaskSetSha256"] = "f" * 64
        if os.name != "nt":
            commitment_path.chmod(0o600)
        commitment_path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
        model, inference = contracts()
        with self.assertRaisesRegex(evaluation.OutcomeError, "does not bind the exact commitment|does not bind this exact commitment and candidate"):
            sealed.guardian_materialize_heldout(
                **self._guardian_kwargs(tuning_root, reveal_root, output, self.parent / "r9.json", self.parent / "held-mat9"),
            )

    # --- candidate drift invalidates held-out reveal before any byte is opened ---
    def test_candidate_drift_invalidates_heldout(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        # A guardian-supplied inference payload for a DIFFERENT candidate (same authoritative
        # freeze) must be rejected before _verify_reveal opens any held-out byte.
        model, inference = contracts()
        other_inference = json.loads(inference.decode("utf-8"))
        other_inference["sampling"]["seed"] = 999
        other_inference["request"]["requestFieldPolicySha256"] = (
            sealed.materializer.outcome_task.inference_policy_sha256(other_inference)
        )
        drifted_mat_root = self.parent / "held-mat-drifted"
        drifted_receipt = self.parent / "drifted-receipt.json"
        kwargs = self._guardian_kwargs(
            tuning_root, reveal_root, output, drifted_receipt, drifted_mat_root,
            inference_payload=json.dumps(other_inference).encode("utf-8"),
        )
        with mock.patch.object(sealed, "_verify_reveal", wraps=sealed._verify_reveal) as spy:
            with self.assertRaisesRegex(evaluation.OutcomeError, "guardian inference payload differs"):
                sealed.guardian_materialize_heldout(**kwargs)
            spy.assert_not_called()
        self.assertFalse(drifted_receipt.exists())
        self.assertFalse(drifted_mat_root.exists())

    # --- materialization drift rejected by the held-out campaign ---
    def test_heldout_materialization_drift_is_rejected(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        heldout_json = json.loads((held_mat_root / "materialization.json").read_text(encoding="utf-8"))
        heldout_json["tasks"][0]["taskSha256"] = "e" * 64
        mat_path = held_mat_root / "materialization.json"
        if os.name != "nt":
            mat_path.chmod(0o600)
        mat_path.write_text(json.dumps(heldout_json) + "\n", encoding="utf-8")
        config = self._pair_system(self.parent, mat)
        execute_pair = real_synthetic_execute_pair()
        with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "does not bind this exact|task set differs|admission differs"):
            campaign.run_campaign(
                root=ROOT, materialization_root=held_mat_root, pair_configuration_path=config,
                output_root=output, max_pairs=1, partition="held-out",
                runtime_condition="cold-first-request", sealed_tuning_root=tuning_root,
                sealed_reveal_receipt=receipt_path, execute_pair=execute_pair,
                sealed_tuning_materialization_root=self.parent / "tune-mat",
                sealed_tuning_output_root=output,
            )

    # --- mismatched preexisting receipt rejects, never ignored ---
    def test_mismatched_preexisting_receipt_is_rejected(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["revealManifestSha256"] = "f" * 64
        if os.name != "nt":
            receipt_path.chmod(0o600)
        receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
        config = self._pair_system(self.parent, mat)
        execute_pair = real_synthetic_execute_pair()
        with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "does not bind this exact freeze, commitment, and held-out materialization"):
            campaign.run_campaign(
                root=ROOT, materialization_root=held_mat_root, pair_configuration_path=config,
                output_root=output, max_pairs=1, partition="held-out",
                runtime_condition="cold-first-request", sealed_tuning_root=tuning_root,
                sealed_reveal_receipt=receipt_path, execute_pair=execute_pair,
                sealed_tuning_materialization_root=self.parent / "tune-mat",
                sealed_tuning_output_root=output,
            )

    # --- held-out run without a reveal receipt rejected ---
    def test_heldout_requires_reveal_receipt(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        config = self._pair_system(self.parent, mat)
        execute_pair = real_synthetic_execute_pair()
        with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "requires an exact reveal receipt"):
            campaign.run_campaign(
                root=ROOT, materialization_root=held_mat_root, pair_configuration_path=config,
                output_root=output, max_pairs=1, partition="held-out",
                runtime_condition="cold-first-request", sealed_tuning_root=tuning_root,
                execute_pair=execute_pair,
                sealed_tuning_materialization_root=self.parent / "tune-mat",
                sealed_tuning_output_root=output,
            )

    # --- reveal receipt + materialization are idempotently converged, never overwritten ---
    def test_reveal_receipt_is_immutable(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        model, inference = contracts()
        before_receipt = receipt_path.read_bytes()
        before_materialization = (held_mat_root / "materialization.json").read_bytes()
        # An exact retry (same output root + receipt) converges to the same valid state.
        retry = sealed.guardian_materialize_heldout(
            **self._guardian_kwargs(tuning_root, reveal_root, output, receipt_path, held_mat_root),
        )
        self.assertEqual(receipt_path.read_bytes(), before_receipt)
        self.assertEqual((held_mat_root / "materialization.json").read_bytes(), before_materialization)
        self.assertEqual(retry["heldOutMaterializationSha256"], guardian["heldOutMaterializationSha256"])

    # --- schemas validate in the normal JSON-schema suite ---
    def test_commitment_and_manifest_validate_against_schemas(self):
        _t, _r, result, sentinel, _tune, _held = self.setup_sealed()
        import jsonschema
        commitment_schema = json.loads(
            (ROOT / "schemas/pixel-sealed-corpus-commitment-v1.schema.json").read_text(encoding="utf-8")
        )
        jsonschema.validate(instance=result["commitment"], schema=commitment_schema)
        reveal = json.loads((_r / "manifest.json").read_text(encoding="utf-8"))
        reveal_schema = json.loads(
            (ROOT / "schemas/pixel-sealed-corpus-reveal-v1.schema.json").read_text(encoding="utf-8")
        )
        jsonschema.validate(instance=reveal, schema=reveal_schema)

    def test_split_is_deterministic(self):
        battery, sentinel = synthetic_battery(
            ("battery-sealed-tune-01", "battery-sealed-tune-02"), ("battery-sealed-hold-01",),
        )
        a = sealed.split_battery(payload(battery), self.parent / "a-t", self.parent / "a-r")
        b = sealed.split_battery(payload(battery), self.parent / "b-t", self.parent / "b-r")
        self.assertEqual(a["commitment"], b["commitment"])
        self.assertEqual(a["commitmentSha256"], b["commitmentSha256"])
        self.assertEqual(a["heldOutTaskSetSha256"], b["heldOutTaskSetSha256"])
        self.assertEqual((a["tuningTaskCount"], a["heldOutTaskCount"]), (2, 1))

    # --- exact tuning-corpus binding: substituted tuning.json bytes reject before materialization ---
    def test_tuning_corpus_substitution_rejected_before_materialization(self):
        tuning_root, _r, result, _s, _tune, _held = self.setup_sealed()
        tuning_path = tuning_root / "tuning.json"
        tuning_path.write_bytes(tuning_path.read_bytes() + b"\n")
        if os.name != "nt":
            tuning_path.chmod(0o600)
        model, inference = contracts()
        with self.assertRaisesRegex(evaluation.OutcomeError, "tuning corpus bytes differ from the exact commitment tuning corpus"):
            sealed.sealed_tuning_materialize(
                tuning_root=tuning_root, root=ROOT, model_payload=model, inference_payload=inference,
                verifier_image_digest="sha256:" + "a" * 64, output_root=self.parent / "tune-mat-bad",
            )

    # --- count-preserving source task substitution rejects before materialization ---
    def test_tuning_count_preserving_task_substitution_rejected_before_materialization(self):
        tuning_root, _r, result, _s, _tune, _held = self.setup_sealed()
        tuning_path = tuning_root / "tuning.json"
        tuning = json.loads(tuning_path.read_text(encoding="utf-8"))
        tuning["tasks"][0]["prompt"] = "Substituted tuning task with identical count."
        if os.name != "nt":
            tuning_path.chmod(0o600)
        tuning_path.write_text(json.dumps(tuning) + "\n", encoding="utf-8")
        model, inference = contracts()
        with self.assertRaises(evaluation.OutcomeError):
            sealed.sealed_tuning_materialize(
                tuning_root=tuning_root, root=ROOT, model_payload=model, inference_payload=inference,
                verifier_image_digest="sha256:" + "a" * 64, output_root=self.parent / "tune-mat-bad2",
            )

    # --- freeze candidate / source / archive / architecture / verifier / model / inference / pair / preflight / runtime mutations reject held-out ---
    def test_freeze_candidate_and_identity_mutations_reject_heldout(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        config = self._pair_system(self.parent, mat)
        freeze_path = output / campaign.FREEZE_FILE
        mutations = {
            "candidate.candidateSourceArchiveSha256": lambda f: f["candidate"].update({"candidateSourceArchiveSha256": "f" * 64}),
            "candidate.architecture": lambda f: f["candidate"].update({"architecture": "arm64"}),
            "candidate.verifierImageDigest": lambda f: f["candidate"].update({"verifierImageDigest": "sha256:" + "b" * 64}),
            "candidate.modelContractSha256": lambda f: f["candidate"].update({"modelContractSha256": "d" * 64}),
            "candidate.inferenceContractSha256": lambda f: f["candidate"].update({"inferenceContractSha256": "d" * 64}),
            "candidate.pairConfigSha256": lambda f: f["candidate"].update({"pairConfigSha256": "d" * 64}),
            "candidate.preflightSha256": lambda f: f["candidate"].update({"preflightSha256": "d" * 64}),
            "candidate.runtimeCondition": lambda f: f["candidate"].update({"runtimeCondition": "warm-neutral-probe"}),
            "candidate.profile": lambda f: f["candidate"].update({"profile": "assistant"}),
            "candidate.evaluationRegime": lambda f: f["candidate"].update({"evaluationRegime": "maximum-quality"}),
            "candidate.candidateId": lambda f: f["candidate"].update({"candidateId": "candidate-" + "9" * 24}),
            "campaignId": lambda f: f.update({"campaignId": "outcomebattery-" + "9" * 24}),
            "materializationSha256": lambda f: f.update({"materializationSha256": "d" * 64}),
            "candidateSourceArchiveSha256": lambda f: f.update({"candidateSourceArchiveSha256": "d" * 64}),
        }
        for name, mutate in mutations.items():
            mutated = json.loads(freeze_path.read_text(encoding="utf-8"))
            mutate(mutated)
            if os.name != "nt":
                freeze_path.chmod(0o600)
            freeze_path.write_text(json.dumps(mutated) + "\n", encoding="utf-8")
            execute_pair = real_synthetic_execute_pair()
            with self.assertRaises(evaluation.OutcomeError, msg=name):
                campaign.run_campaign(
                    root=ROOT, materialization_root=held_mat_root, pair_configuration_path=config,
                    output_root=output, max_pairs=1, partition="held-out",
                    runtime_condition="cold-first-request", sealed_tuning_root=tuning_root,
                    sealed_reveal_receipt=receipt_path, execute_pair=execute_pair,
                    sealed_tuning_materialization_root=self.parent / "tune-mat",
                    sealed_tuning_output_root=output,
                )
            # restore the exact freeze before the next mutation
            if os.name != "nt":
                freeze_path.chmod(0o600)
            freeze_path.write_text(json.dumps(freeze) + "\n", encoding="utf-8")

    # --- campaign.json identity mutation rejects held-out ---
    def test_campaign_identity_mutation_rejects_heldout(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        config = self._pair_system(self.parent, mat)
        campaign_path = output / "campaign.json"
        identity = json.loads(campaign_path.read_text(encoding="utf-8"))
        identity["campaignId"] = "outcomebattery-" + "9" * 24
        if os.name != "nt":
            campaign_path.chmod(0o600)
        campaign_path.write_text(json.dumps(identity) + "\n", encoding="utf-8")
        execute_pair = real_synthetic_execute_pair()
        with self.assertRaisesRegex(evaluation.OutcomeError, "held-out campaign identity differs from the recomputed original tuning campaign"):
            campaign.run_campaign(
                root=ROOT, materialization_root=held_mat_root, pair_configuration_path=config,
                output_root=output, max_pairs=1, partition="held-out",
                runtime_condition="cold-first-request", sealed_tuning_root=tuning_root,
                sealed_reveal_receipt=receipt_path, execute_pair=execute_pair,
                sealed_tuning_materialization_root=self.parent / "tune-mat",
                sealed_tuning_output_root=output,
            )

    # --- freeze tuning list reorder / duplicate / hash / classification / attempt mutation rejects internally ---
    def test_freeze_tuning_list_mutations_reject_internally(self):
        (_t, _r, _result, _s, _tune, _held, _mat, output, freeze, _receipt, _hmat, _g) = self.run_full_flow()
        base = json.loads(json.dumps(freeze))
        reorder = json.loads(json.dumps(freeze))
        reorder["tuningTasks"] = list(reversed(reorder["tuningTasks"]))
        with self.assertRaisesRegex(evaluation.OutcomeError, "canonically sorted"):
            sealed._validate_freeze(reorder)
        duplicate = json.loads(json.dumps(freeze))
        duplicate["tuningTasks"][1] = dict(duplicate["tuningTasks"][0])
        with self.assertRaisesRegex(evaluation.OutcomeError, "duplicated"):
            sealed._validate_freeze(duplicate)
        bad_hash = json.loads(json.dumps(freeze))
        bad_hash["tuningTasks"][0]["taskSha256"] = "f" * 64
        with self.assertRaisesRegex(evaluation.OutcomeError, "materialized task set digest is inconsistent"):
            sealed._validate_freeze(bad_hash)
        bad_materialized = json.loads(json.dumps(freeze))
        bad_materialized["tuningMaterializedTaskSetSha256"] = "f" * 64
        with self.assertRaisesRegex(evaluation.OutcomeError, "inconsistent"):
            sealed._validate_freeze(bad_materialized)
        bad_evidence = json.loads(json.dumps(freeze))
        bad_evidence["tuningEvidenceSetSha256"] = "f" * 64
        with self.assertRaisesRegex(evaluation.OutcomeError, "evidence set digest is inconsistent"):
            sealed._validate_freeze(bad_evidence)
        bad_classification = json.loads(json.dumps(freeze))
        bad_classification["tuningTasks"][0]["classification"] = "not-a-class"
        with self.assertRaisesRegex(evaluation.OutcomeError, "classification is invalid"):
            sealed._validate_freeze(bad_classification)
        bad_attempt = json.loads(json.dumps(freeze))
        bad_attempt["tuningTasks"][0]["attempt"] = 0
        with self.assertRaisesRegex(evaluation.OutcomeError, "not a passing baseline"):
            sealed._validate_freeze(bad_attempt)
        sealed._validate_freeze(base)

    # --- held-out recomputes original tuning pair evidence and rejects a tampered tuning pair ---
    def test_heldout_recomputes_tuning_evidence_and_rejects_tampered_pair(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        config = self._pair_system(self.parent, mat)
        tampered = output / "pairs" / "battery-sealed-tune-01" / "attempt-001" / "pixel" / "run.json"
        original = tampered.read_bytes()
        tampered.write_bytes(original + b" tampered")
        if os.name != "nt":
            tampered.chmod(0o600)
        execute_pair = real_synthetic_execute_pair()
        with self.assertRaisesRegex(evaluation.OutcomeError, "differs from recomputed run comparison|differs from recomputed evidence|not valid JSON"):
            campaign.run_campaign(
                root=ROOT, materialization_root=held_mat_root, pair_configuration_path=config,
                output_root=output, max_pairs=1, partition="held-out",
                runtime_condition="cold-first-request", sealed_tuning_root=tuning_root,
                sealed_reveal_receipt=receipt_path, execute_pair=execute_pair,
                sealed_tuning_materialization_root=self.parent / "tune-mat",
                sealed_tuning_output_root=output,
            )

    # --- guardian settlement crash-window convergence and mismatch rejection ---
    def test_guardian_settlement_crash_convergence(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        model, inference = contracts()

        def settle(hook):
            sealed._guardian_hooks[hook] = lambda: (_ for _ in ()).throw(RuntimeError("crash"))
            try:
                with self.assertRaises(RuntimeError):
                    sealed.guardian_materialize_heldout(
                        **self._guardian_kwargs(tuning_root, reveal_root, output, receipt_path, held_mat_root),
                    )
            finally:
                sealed._guardian_hooks.clear()
            # exact retry converges to one valid state without overwriting anything
            retry = sealed.guardian_materialize_heldout(
                **self._guardian_kwargs(tuning_root, reveal_root, output, receipt_path, held_mat_root),
            )
            self.assertEqual(retry["heldOutMaterializationSha256"], guardian["heldOutMaterializationSha256"])
            self.assertTrue(receipt_path.is_file())

        settle("before_materialization_settlement")
        settle("after_materialization_settlement")
        settle("after_receipt")

    def test_guardian_settlement_substituted_materialization_rejects(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        model, inference = contracts()
        mat_path = held_mat_root / "materialization.json"
        drifted = json.loads(mat_path.read_text(encoding="utf-8"))
        drifted["tasks"][0]["taskSha256"] = "f" * 64
        if os.name != "nt":
            mat_path.chmod(0o600)
        mat_path.write_text(json.dumps(drifted) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(evaluation.OutcomeError, "preexisting held-out materialization differs"):
            sealed.guardian_materialize_heldout(
                **self._guardian_kwargs(tuning_root, reveal_root, output, receipt_path, held_mat_root),
            )

    # --- adversarial: forged but schema-valid, internally self-consistent freeze is rejected before reveal ---
    def test_forged_self_consistent_freeze_rejected_before_reveal(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        # A fabricated freeze that passes _validate_freeze (correct commitment aggregates,
        # valid candidate, invented passing tuning entries with matching internal set digests)
        # must NOT authorize disclosure because it is not derived from the original tuning evidence.
        forged = json.loads(json.dumps(freeze))
        forged["tuningTasks"] = [
            {"batteryTaskId": task_id, "taskSha256": "a" * 64, "attempt": 1,
             "comparisonSha256": "b" * 64, "status": "pass", "classification": "parity"}
            for task_id in tune
        ]
        forged["tuningMaterializedTaskSetSha256"] = sealed._tuning_materialized_set_sha(forged["tuningTasks"])
        forged["tuningEvidenceSetSha256"] = sealed._tuning_evidence_set_sha(forged["tuningTasks"])
        # prove the forged freeze is internally consistent and schema-valid
        sealed._validate_freeze(forged)
        freeze_path = output / campaign.FREEZE_FILE
        if os.name != "nt":
            freeze_path.chmod(0o600)
        freeze_path.write_text(json.dumps(forged) + "\n", encoding="utf-8")
        kwargs = self._guardian_kwargs(
            tuning_root, reveal_root, output, self.parent / "forged-receipt.json", self.parent / "forged-mat",
        )
        self._assert_rejected_before_reveal(kwargs, "guardian freeze differs from the recomputed")
        self.assertFalse((self.parent / "forged-receipt.json").exists())
        self.assertFalse((self.parent / "forged-mat").exists())

    # --- adversarial: tampered original campaign.json rejected before reveal ---
    def test_tampered_campaign_json_rejected_before_reveal(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        campaign_path = output / "campaign.json"
        identity = json.loads(campaign_path.read_text(encoding="utf-8"))
        identity["campaignId"] = "outcomebattery-" + "9" * 24
        if os.name != "nt":
            campaign_path.chmod(0o600)
        campaign_path.write_text(json.dumps(identity) + "\n", encoding="utf-8")
        kwargs = self._guardian_kwargs(
            tuning_root, reveal_root, output, self.parent / "campaign-receipt.json", self.parent / "campaign-mat",
        )
        self._assert_rejected_before_reveal(kwargs, "original tuning campaign identity differs")

    # --- adversarial: missing original freeze rejected before reveal ---
    def test_missing_original_freeze_rejected_before_reveal(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        (output / campaign.FREEZE_FILE).unlink()
        kwargs = self._guardian_kwargs(
            tuning_root, reveal_root, output, self.parent / "missing-receipt.json", self.parent / "missing-mat",
        )
        self._assert_rejected_before_reveal(kwargs, "requires an exact pre-disclosure tuning freeze")

    # --- adversarial: tampered original tuning materialization rejected before reveal ---
    def test_tampered_tuning_materialization_rejected_before_reveal(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        mat_path = self.parent / "tune-mat" / "materialization.json"
        materialized = json.loads(mat_path.read_text(encoding="utf-8"))
        materialized["tasks"][0]["taskSha256"] = "f" * 64
        if os.name != "nt":
            mat_path.chmod(0o600)
        mat_path.write_text(json.dumps(materialized) + "\n", encoding="utf-8")
        kwargs = self._guardian_kwargs(
            tuning_root, reveal_root, output, self.parent / "tamper-mat-receipt.json", self.parent / "tamper-mat",
        )
        self._assert_rejected_before_reveal(kwargs, "differ from their inventory|does not bind the exact commitment|freeze differs")

    # --- adversarial: tampered original tuning pair evidence rejected before reveal ---
    def test_tampered_tuning_pair_evidence_rejected_before_reveal(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        run = output / "pairs" / "battery-sealed-tune-01" / "attempt-001" / "pixel" / "run.json"
        original = run.read_bytes()
        run.write_bytes(original + b" tampered")
        if os.name != "nt":
            run.chmod(0o600)
        kwargs = self._guardian_kwargs(
            tuning_root, reveal_root, output, self.parent / "pair-receipt.json", self.parent / "pair-mat",
        )
        self._assert_rejected_before_reveal(kwargs, "differs from recomputed|differs from recomputed evidence|not valid JSON")

    # --- adversarial: wrong guardian candidate/input values rejected before reveal ---
    def test_wrong_guardian_inputs_rejected_before_reveal(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        model, inference = contracts()
        cases = [
            ("model payload", {"model_payload": model + b" tampered"},
             "guardian model payload differs"),
            ("verifier digest", {"verifier_image_digest": "sha256:" + "b" * 64},
             "guardian verifier image digest differs"),
            ("architecture", {"architecture": "arm64"}, "guardian architecture differs"),
            ("profile", {"profile": "assistant"}, "guardian profile differs"),
            ("evaluation regime", {"evaluation_regime": "maximum-quality"},
             "guardian evaluation regime differs"),
            ("runtime condition", {"runtime_condition": "warm-neutral-probe"},
             "arm order mismatch|guardian runtime condition differs|paired execution index identity or task binding is invalid"),
        ]
        for name, override, regex in cases:
            kwargs = self._guardian_kwargs(
                tuning_root, reveal_root, output, self.parent / f"{name}-receipt.json".replace(" ", "-"),
                self.parent / f"{name}-mat".replace(" ", "-"),
                **override,
            )
            self._assert_rejected_before_reveal(kwargs, regex)

    # --- adversarial: staged materialization.json is syntactically valid but
    # candidate-mismatched; guardian must reject before settlement and receipt. ---
    def test_guardian_rejects_candidate_mismatched_staged_materialization_before_settlement(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        real_materialize = sealed.materializer.materialize

        def mismatched_staged_file(**kwargs):
            returned = real_materialize(**kwargs)
            staging = kwargs["output_root"]
            path = staging / "materialization.json"
            drifted = json.loads(path.read_text(encoding="utf-8"))
            drifted["batterySha256"] = "f" * 64
            if os.name != "nt":
                path.chmod(0o600)
            path.write_text(json.dumps(drifted) + "\n", encoding="utf-8")
            # Return the same drifted object so only the binding validator can catch it.
            return drifted

        new_receipt = self.parent / "adv-mismatch-receipt.json"
        new_mat = self.parent / "adv-mismatch-mat"
        kwargs = self._guardian_kwargs(
            tuning_root, reveal_root, output, new_receipt, new_mat,
        )
        with mock.patch.object(sealed.materializer, "materialize", side_effect=mismatched_staged_file):
            with self.assertRaisesRegex(evaluation.OutcomeError, "does not bind the exact frozen candidate and commitment"):
                sealed.guardian_materialize_heldout(**kwargs)
        self.assertFalse(new_receipt.exists())
        self.assertFalse(new_mat.exists())

    # --- adversarial: staged return-object/file mismatch must reject before settlement. ---
    def test_guardian_rejects_staged_return_object_file_mismatch(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        real_materialize = sealed.materializer.materialize

        def mismatched_return(**kwargs):
            returned = real_materialize(**kwargs)
            drifted = json.loads(json.dumps(returned))
            drifted["tasks"] = list(drifted["tasks"])
            drifted["tasks"][0] = dict(drifted["tasks"][0])
            drifted["tasks"][0]["taskSha256"] = "f" * 64
            return drifted

        new_receipt = self.parent / "adv-return-receipt.json"
        new_mat = self.parent / "adv-return-mat"
        kwargs = self._guardian_kwargs(
            tuning_root, reveal_root, output, new_receipt, new_mat,
        )
        with mock.patch.object(sealed.materializer, "materialize", side_effect=mismatched_return):
            with self.assertRaisesRegex(evaluation.OutcomeError, "return value differs from its staged authoritative file"):
                sealed.guardian_materialize_heldout(**kwargs)
        self.assertFalse(new_receipt.exists())
        self.assertFalse(new_mat.exists())

    # --- adversarial: tuning materialization with a wrong batterySha256 must reject
    # before _verify_reveal and leave no output/receipt. ---
    def test_tuning_materialization_wrong_battery_sha_rejected_before_reveal(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        mat_path = self.parent / "tune-mat" / "materialization.json"
        materialized = json.loads(mat_path.read_text(encoding="utf-8"))
        materialized["batterySha256"] = "f" * 64
        if os.name != "nt":
            mat_path.chmod(0o600)
        mat_path.write_text(json.dumps(materialized) + "\n", encoding="utf-8")
        new_receipt = self.parent / "adv-battery-receipt.json"
        new_mat = self.parent / "adv-battery-mat"
        kwargs = self._guardian_kwargs(tuning_root, reveal_root, output, new_receipt, new_mat)
        self._assert_rejected_before_reveal(kwargs, "sealed tuning materialization battery differs")
        self.assertFalse(new_receipt.exists())
        self.assertFalse(new_mat.exists())

    # --- the successful guardian path must exercise the real _valid_pair recomputation. ---
    def test_success_path_uses_real_valid_pair(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        new_receipt = self.parent / "real-vp-receipt.json"
        new_mat = self.parent / "real-vp-mat"
        with mock.patch.object(campaign, "_valid_pair", wraps=campaign._valid_pair) as spy:
            retry = sealed.guardian_materialize_heldout(
                **self._guardian_kwargs(tuning_root, reveal_root, output, new_receipt, new_mat),
            )
            self.assertGreaterEqual(spy.call_count, 1)
        self.assertTrue(new_receipt.is_file())
        self.assertTrue((new_mat / "materialization.json").is_file())

    # --- CLI split requires owner-private, single-link, duplicate-key-safe bounded input ---
    def _cli_split(self, battery_payload, battery_mode=None):
        battery_path = self.parent / "battery.json"
        battery_path.write_bytes(battery_payload)
        if os.name != "nt" and battery_mode is not None:
            battery_path.chmod(battery_mode)
        import subprocess
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/portal_outcome_sealed_corpus.py"), "split",
             "--battery", str(battery_path), "--tuning-root", str(self.parent / "cli-t"),
             "--reveal-root", str(self.parent / "cli-r")],
            capture_output=True, text=True,
        )
        return result

    def test_cli_split_requires_owner_private_battery(self):
        if os.name == "nt":
            return
        battery, _s = synthetic_battery(("battery-sealed-tune-01",), ("battery-sealed-hold-01",))
        result = self._cli_split(payload(battery), battery_mode=0o644)
        self.assertEqual(result.returncode, 2)
        self.assertIn("owner-bound mode 0600", result.stderr)

    def test_cli_split_rejects_symlink_and_hardlink_battery(self):
        if os.name == "nt":
            return
        battery, _s = synthetic_battery(("battery-sealed-tune-01",), ("battery-sealed-hold-01",))
        import subprocess
        real = self.parent / "real.json"
        real.write_bytes(payload(battery))
        real.chmod(0o600)
        link = self.parent / "battery-link.json"
        link.symlink_to(real)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/portal_outcome_sealed_corpus.py"), "split",
             "--battery", str(link), "--tuning-root", str(self.parent / "cli-t"),
             "--reveal-root", str(self.parent / "cli-r")],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unavailable or unsafe", result.stderr)
        os.unlink(link)
        hard = self.parent / "battery-hard.json"
        os.link(real, hard)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/portal_outcome_sealed_corpus.py"), "split",
             "--battery", str(hard), "--tuning-root", str(self.parent / "cli-t2"),
             "--reveal-root", str(self.parent / "cli-r2")],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("single-link", result.stderr)

    def test_cli_split_rejects_duplicate_keys(self):
        battery, _s = synthetic_battery(("battery-sealed-tune-01",), ("battery-sealed-hold-01",))
        head = json.dumps({k: v for k, v in battery.items() if k != "tasks"}, sort_keys=True)[:-1]
        dup = head + ', "tasks": [], "tasks": ' + json.dumps(battery["tasks"], sort_keys=True) + '}'
        result = self._cli_split(dup.encode("utf-8"), battery_mode=0o600 if os.name != "nt" else None)
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate fields", result.stderr)

    # --- aggregate/input bound enforced with a small cap ---
    def test_aggregate_bound_enforced(self):
        battery, _s = synthetic_battery(("battery-sealed-tune-01",), ("battery-sealed-hold-01",))
        small = 512
        with mock.patch.object(sealed, "MAX_CORPUS_BYTES", small), mock.patch.object(sealed, "MAX_BATTERY_BYTES", small):
            with self.assertRaisesRegex(evaluation.OutcomeError, "oversized"):
                sealed.split_battery(payload(battery), self.parent / "agg-t", self.parent / "agg-r")

    # --- CLI guardian-materialize + validate operator seams ---
    def test_cli_guardian_materialize_and_validate_seams(self):
        (tuning_root, reveal_root, result, sentinel, tune, held, mat, output, freeze,
         receipt_path, held_mat_root, guardian) = self.run_full_flow()
        import subprocess
        model, inference = contracts()
        model_path = self.parent / "model.json"
        inference_path = self.parent / "inference.json"
        model_path.write_bytes(model)
        inference_path.write_bytes(inference)
        if os.name != "nt":
            model_path.chmod(0o600)
            inference_path.chmod(0o600)
        new_receipt = self.parent / "cli-receipt.json"
        new_mat = self.parent / "cli-holdout"
        script = str(ROOT / "scripts/portal_outcome_sealed_corpus.py")
        result = subprocess.run(
            [sys.executable, script, "guardian-materialize",
             "--root", str(ROOT), "--reveal-root", str(reveal_root), "--tuning-root", str(tuning_root),
             "--freeze", str(output / campaign.FREEZE_FILE), "--receipt", str(new_receipt),
             "--model", str(model_path), "--inference", str(inference_path),
             "--verifier-image-digest", "sha256:" + "a" * 64, "--output-root", str(new_mat),
             "--tuning-materialization-root", str(self.parent / "tune-mat"),
             "--tuning-output-root", str(output),
             "--pair-configuration-path", str(self.pair_config)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        validate = subprocess.run(
            [sys.executable, script, "validate",
             "--tuning-root", str(tuning_root), "--freeze", str(output / campaign.FREEZE_FILE),
             "--receipt", str(new_receipt), "--heldout-materialization-root", str(new_mat)],
            capture_output=True, text=True,
        )
        self.assertEqual(validate.returncode, 0, validate.stderr)
        self.assertIn("commitmentSha256", validate.stdout)


    def test_cli_tuning_materialize_on_synthetic_tuning_root_no_reveal_access(self):
        tuning_root, reveal_root, result, sentinel, tune, held = self.setup_sealed()
        reveal_before = {path.name: path.read_bytes() for path in sorted(reveal_root.iterdir())}
        model, inference = contracts()
        model_path = self.parent / "tune-model.json"
        inference_path = self.parent / "tune-inference.json"
        model_path.write_bytes(model)
        inference_path.write_bytes(inference)
        if os.name != "nt":
            model_path.chmod(0o600)
            inference_path.chmod(0o600)
        output_root = self.parent / "cli-tune-mat"
        script = str(ROOT / "scripts/portal_outcome_sealed_corpus.py")
        import subprocess
        proc = subprocess.run(
            [sys.executable, script, "tuning-materialize",
             "--tuning-root", str(tuning_root), "--model", str(model_path),
             "--inference", str(inference_path),
             "--verifier-image-digest", "sha256:" + "a" * 64,
             "--output-root", str(output_root)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["operation"], "pixel-portal-outcome-sealed-tuning-materialization")
        self.assertEqual(summary["mode"], "sealed")
        self.assertEqual(summary["sealedCommitmentSha256"], result["commitmentSha256"])
        self.assertEqual(summary["tuningTaskCount"], 2)
        self.assertNotIn(sentinel, proc.stdout)
        self.assertNotIn(held[0], proc.stdout)
        self.assertNotIn(b"python3", proc.stdout.encode())
        reveal_after = {path.name: path.read_bytes() for path in sorted(reveal_root.iterdir())}
        self.assertEqual(reveal_before, reveal_after)

    def test_cli_tuning_materialize_rejects_tampered_tuning_without_output(self):
        tuning_root, reveal_root, result, sentinel, tune, held = self.setup_sealed()
        (tuning_root / "tuning.json").write_text("{}", encoding="utf-8")
        if os.name != "nt":
            (tuning_root / "tuning.json").chmod(0o600)
        model, inference = contracts()
        model_path = self.parent / "tune-model.json"
        inference_path = self.parent / "tune-inference.json"
        for path, data in ((model_path, model), (inference_path, inference)):
            path.write_bytes(data)
            if os.name != "nt":
                path.chmod(0o600)
        output_root = self.parent / "cli-tune-mat"
        script = str(ROOT / "scripts/portal_outcome_sealed_corpus.py")
        import subprocess
        proc = subprocess.run(
            [sys.executable, script, "tuning-materialize",
             "--tuning-root", str(tuning_root), "--model", str(model_path),
             "--inference", str(inference_path),
             "--verifier-image-digest", "sha256:" + "a" * 64,
             "--output-root", str(output_root)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(output_root.exists())


if __name__ == "__main__":
    unittest.main()
