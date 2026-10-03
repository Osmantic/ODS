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
import portal_outcome_battery_campaign as campaign
from tests.test_portal_outcome_materialize_battery import contracts, payload


def private_json(path: Path, value) -> None:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


class PortalOutcomeBatteryCampaignTests(unittest.TestCase):
    def setUp(self):
        self.compatibility_calls = []
        self.compatibility_patch = mock.patch.object(
            campaign.pair_preflight, "review_materialized_pixel_task_compatibility",
            side_effect=self.compatibility_review,
        )
        self.compatibility_patch.start()
        self.addCleanup(self.compatibility_patch.stop)

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

    def fixture(self, parent: Path, *, heldout_focus: bool = False, evaluation_regime: str = "matched-budget"):
        original = json.loads((ROOT / "security-evals/agent-comparison/task-battery-v1.json").read_text(encoding="utf-8"))
        tuning = [
            task for task in original["tasks"]
            if task.get("partition", task.get("rehearsal", {}).get("partition", "tuning")) == "tuning"
        ]
        heldout = next(
            task for task in original["tasks"]
            if task.get("partition", task.get("rehearsal", {}).get("partition", "tuning")) == "held-out"
        )
        original["tasks"] = [tuning[0], heldout] if heldout_focus else [*tuning[:2], heldout]
        model_payload, inference_payload = contracts()
        materialization = parent / "materialization"
        campaign.materializer.materialize(
            root=ROOT, battery_payload=payload(original), model_payload=model_payload,
            inference_payload=inference_payload, verifier_image_digest="sha256:" + "a" * 64,
            output_root=materialization, evaluation_regime=evaluation_regime,
        )
        model = parent / "model"
        model.mkdir()
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
        materialized = json.loads((materialization / "materialization.json").read_text(encoding="utf-8"))
        digest = lambda content: hashlib.sha256(content).hexdigest()
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
        return materialization, config, original["tasks"]

    def test_maximum_quality_cannot_impersonate_or_overwrite_the_matched_budget_freeze(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            materialization, config, _tasks = self.fixture(parent, evaluation_regime="maximum-quality")
            output = parent / "campaign"
            with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "separate frozen matched-budget baseline"):
                campaign.run_campaign(
                    root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                    output_root=output, max_pairs=1, freeze_tuning=True,
                    execute_pair=lambda **_options: self.fail("maximum-quality must not reach paired execution before baseline freeze"),
                )
            self.assertFalse(output.exists())

    def test_campaign_resumes_without_overwrite_and_alternates_arm_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            materialization, config, tasks = self.fixture(parent)
            output = parent / "campaign"
            completed = {}
            calls = []

            def execute_pair(**options):
                attempt = options["output_root"]
                campaign.pair_runner._new_private_directory(attempt)
                (attempt / "pair.json").write_text("{}\n", encoding="utf-8")
                if os.name != "nt":
                    (attempt / "pair.json").chmod(0o600)
                task_id = options["task_path"].parent.name
                calls.append((task_id, options["order"], attempt.name))
                value = {
                    "schemaVersion": 1, "operation": "pixel-portal-outcome-pair-execution",
                    "taskSha256": "1" * 64, "taskAdmissionSha256": "2" * 64,
                    "configurationSha256": "7" * 64, "preflightSha256": "8" * 64,
                    "modelContractSha256": "9" * 64, "inferenceContractSha256": "a" * 64,
                    "runtimeCondition": options["runtime_condition"],
                    "armOrder": ["pixel", "codex"] if options["order"] == "pixel-first" else ["codex", "pixel"],
                    "pixelRunSha256": "3" * 64, "codexRunSha256": "4" * 64,
                    "comparisonSha256": ("5" if len(calls) == 1 else "6") * 64,
                    "status": "pass", "classification": "parity", "boundary": campaign.pair_runner.PAIR_BOUNDARY,
                }
                completed[attempt] = value
                return value

            def valid_pair(_root, _task_path, attempt, expected_order, _expected_bindings, expected_runtime_condition):
                value = completed.get(attempt)
                if value is not None:
                    self.assertEqual(value["armOrder"], list(expected_order))
                    self.assertEqual(value["runtimeCondition"], expected_runtime_condition)
                return value

            with mock.patch.object(campaign, "_valid_pair", side_effect=valid_pair):
                first = campaign.run_campaign(
                    root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                    output_root=output, max_pairs=1, execute_pair=execute_pair,
                )
                second = campaign.run_campaign(
                    root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                    output_root=output, max_pairs=1, execute_pair=execute_pair,
                )
                third = campaign.run_campaign(
                    root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                    output_root=output, max_pairs=2, execute_pair=execute_pair,
                )
            self.assertEqual(first["status"], "in-progress")
            self.assertEqual(second["status"], "pass")
            self.assertEqual(third["executedThisInvocation"], 0)
            self.assertEqual(third["compatibilityReviewedTasks"], 2)
            self.assertEqual([item[:2] for item in calls], [
                (tasks[0]["taskId"], "pixel-first"), (tasks[1]["taskId"], "codex-first"),
            ])
            self.assertTrue((output / "campaign.json").is_file())
            self.assertEqual(len(list(output.glob("progress-*.json"))), 3)
            self.assertNotIn(str(parent), json.dumps(second))

    def test_warm_condition_reverses_every_predeclared_cold_arm_order(self):
        for index in range(12):
            cold = campaign._arm_order(index, "cold-first-request")
            warm = campaign._arm_order(index, "warm-neutral-probe")
            self.assertEqual(warm, tuple(reversed(cold)))
        with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "runtime condition"):
            campaign._arm_order(0, "unknown")

    def test_shared_preflight_accepts_distinct_launch_bundle_hashes_per_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            materialization, config, _tasks = self.fixture(parent)
            configuration, _raw = campaign.pair_runner.load_configuration_record(config)
            preflight, _preflight_raw = campaign.pair_runner.load_preflight(Path(configuration["preflightPath"]))
            seen = {}

            def reviewer(**options):
                base = self.compatibility_review(**options)
                # Each admitted task carries its own fully hashed launch bundle;
                # a shared preflight must not force a single cross-task value.
                distinct = hashlib.sha256(str(options["task_path"]).encode("utf-8")).hexdigest()
                base["launchBundleSha256"] = distinct
                seen[options["task_path"]] = distinct
                return base

            reviewed = campaign._review_partition_task_compatibility(
                root=ROOT, materialization_root=materialization, materialization=json.loads(
                    (materialization / "materialization.json").read_text(encoding="utf-8"),
                ),
                partition="tuning", pair_configuration_path=config, preflight=preflight,
                temporary_parent=parent, reviewer=reviewer,
            )
            self.assertGreaterEqual(reviewed, 2)
            self.assertGreater(len(set(seen.values())), 1, "distinct per-task launch hashes must be accepted")

    def test_shared_preflight_rejects_task_invariant_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            materialization, config, _tasks = self.fixture(parent)
            configuration, _raw = campaign.pair_runner.load_configuration_record(config)
            preflight, _preflight_raw = campaign.pair_runner.load_preflight(Path(configuration["preflightPath"]))

            def reviewer(**options):
                base = self.compatibility_review(**options)
                base["workPolicySha256"] = "f" * 64
                return base

            with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "differs from the exact qualified preflight"):
                campaign._review_partition_task_compatibility(
                    root=ROOT, materialization_root=materialization, materialization=json.loads(
                        (materialization / "materialization.json").read_text(encoding="utf-8"),
                    ),
                    partition="tuning", pair_configuration_path=config, preflight=preflight,
                    temporary_parent=parent, reviewer=reviewer,
                )

    def test_campaign_reviews_every_task_before_starting_the_first_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            materialization, config, _tasks = self.fixture(parent)
            output = parent / "campaign"
            calls = 0

            def reviewer(**options):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise campaign.evaluation.OutcomeError("synthetic incompatible task")
                return self.compatibility_review(**options)

            with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "synthetic incompatible task"):
                campaign.run_campaign(
                    root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                    output_root=output, max_pairs=1, review_task_compatibility=reviewer,
                    execute_pair=lambda **_options: self.fail("campaign must review every task before execution"),
                )
            self.assertEqual(calls, 2)
            self.assertFalse(output.exists())

    def test_tuning_and_freeze_do_not_open_heldout_task_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            materialization, config, tasks = self.fixture(parent, heldout_focus=True)
            output = parent / "campaign"
            heldout_task = next(
                task for task in tasks
                if task.get("partition", task.get("rehearsal", {}).get("partition", "tuning")) == "held-out"
            )
            heldout_path = materialization / heldout_task["taskId"] / "task.json"
            heldout_path.write_bytes(b"held-out bytes must remain unopened before freeze\n")
            if os.name != "nt":
                heldout_path.chmod(0o600)
            completed = {}

            def execute_pair(**options):
                attempt = options["output_root"]
                campaign.pair_runner._new_private_directory(attempt)
                private_json(attempt / "pair.json", {})
                value = {
                    "schemaVersion": 1, "operation": "pixel-portal-outcome-pair-execution",
                    "taskSha256": "1" * 64, "taskAdmissionSha256": "2" * 64,
                    "configurationSha256": "7" * 64, "preflightSha256": "8" * 64,
                    "modelContractSha256": "9" * 64, "inferenceContractSha256": "a" * 64,
                    "runtimeCondition": options["runtime_condition"], "armOrder": ["pixel", "codex"],
                    "pixelRunSha256": "3" * 64, "codexRunSha256": "4" * 64,
                    "comparisonSha256": "5" * 64, "status": "pass", "classification": "parity",
                    "boundary": campaign.pair_runner.PAIR_BOUNDARY,
                }
                completed[attempt] = value
                return value

            def valid_pair(_root, _task_path, attempt, expected_order, _expected_bindings, expected_runtime_condition):
                value = completed.get(attempt)
                if value is not None:
                    self.assertEqual(list(expected_order), ["pixel", "codex"])
                    self.assertEqual(value["runtimeCondition"], expected_runtime_condition)
                return value

            with mock.patch.object(campaign, "_valid_pair", side_effect=valid_pair):
                tuning = campaign.run_campaign(
                    root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                    output_root=output, max_pairs=1, partition="tuning", execute_pair=execute_pair,
                )
                self.assertEqual(tuning["status"], "pass")
                self.assertEqual(tuning["compatibilityReviewedTasks"], 1)
                self.assertEqual([path.parent.name for path in self.compatibility_calls], [tasks[0]["taskId"]])
                frozen = campaign.run_campaign(
                    root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                    output_root=output, max_pairs=1, partition="tuning", freeze_tuning=True,
                    execute_pair=execute_pair,
                )
                self.assertEqual(frozen["operation"], "pixel-portal-outcome-tuning-baseline-freeze")
                with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "task bytes differ"):
                    campaign.run_campaign(
                        root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                        output_root=output, max_pairs=1, partition="held-out", execute_pair=execute_pair,
                    )

    def test_campaign_refuses_configuration_drift_after_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            materialization, config, _tasks = self.fixture(parent)
            config.write_text(config.read_text(encoding="utf-8") + " ", encoding="utf-8")
            if os.name != "nt":
                config.chmod(0o600)
            output = parent / "campaign"
            with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "differs from its preflight"):
                campaign.run_campaign(
                    root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                    output_root=output, max_pairs=1,
                    execute_pair=lambda **_options: self.fail("drifted campaign must not execute a pair"),
                )
            self.assertFalse(output.exists())

    def test_completed_pair_with_wrong_predeclared_order_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            task = parent / "task.json"
            private_json(task, {"task": True})
            attempt = parent / "attempt-001"
            attempt.mkdir(mode=0o700)
            private_json(attempt / "pair.json", {
                "schemaVersion": 1, "operation": "pixel-portal-outcome-pair-execution",
                "taskSha256": campaign.evaluation.sha256(task.read_bytes()),
                "taskAdmissionSha256": "2" * 64, "armOrder": ["codex", "pixel"],
                "configurationSha256": "6" * 64, "preflightSha256": "7" * 64,
                "modelContractSha256": "8" * 64, "inferenceContractSha256": "9" * 64,
                "runtimeCondition": "cold-first-request",
                "pixelRunSha256": "3" * 64, "codexRunSha256": "4" * 64,
                "comparisonSha256": "5" * 64, "status": "pass", "classification": "parity",
                "boundary": campaign.pair_runner.PAIR_BOUNDARY,
            })
            admission = {"comparisonLane": "same-model-harness"}
            with mock.patch.object(campaign.outcome_task, "admit_task", return_value=admission):
                with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "identity or task binding"):
                    campaign._valid_pair(ROOT, task, attempt, ("pixel", "codex"), {
                        "configurationSha256": "6" * 64, "preflightSha256": "7" * 64,
                        "modelContractSha256": "8" * 64, "inferenceContractSha256": "9" * 64,
                    }, "cold-first-request")

    def test_heldout_partition_requires_immutable_tuning_freeze_and_forbids_retuning(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            materialization, config, tasks = self.fixture(parent, heldout_focus=True)
            output = parent / "campaign"
            completed = {}

            def execute_pair(**options):
                attempt = options["output_root"]
                campaign.pair_runner._new_private_directory(attempt)
                (attempt / "pair.json").write_text("{}\n", encoding="utf-8")
                if os.name != "nt":
                    (attempt / "pair.json").chmod(0o600)
                task_id = options["task_path"].parent.name
                index = next(i for i, task in enumerate(tasks) if task["taskId"] == task_id)
                value = {
                    "schemaVersion": 1, "operation": "pixel-portal-outcome-pair-execution",
                    "taskSha256": "1" * 64, "taskAdmissionSha256": "2" * 64,
                    "configurationSha256": "7" * 64, "preflightSha256": "8" * 64,
                    "modelContractSha256": "9" * 64, "inferenceContractSha256": "a" * 64,
                    "runtimeCondition": options["runtime_condition"],
                    "armOrder": ["pixel", "codex"] if index % 2 == 0 else ["codex", "pixel"],
                    "pixelRunSha256": "3" * 64, "codexRunSha256": "4" * 64,
                    "comparisonSha256": ("5" if index == 0 else "6") * 64,
                    "status": "pass", "classification": "parity", "boundary": campaign.pair_runner.PAIR_BOUNDARY,
                }
                completed[attempt] = value
                return value

            def valid_pair(_root, _task_path, attempt, expected_order, _expected_bindings, expected_runtime_condition):
                value = completed.get(attempt)
                if value is not None:
                    self.assertEqual(value["armOrder"], list(expected_order))
                    self.assertEqual(value["runtimeCondition"], expected_runtime_condition)
                return value

            with mock.patch.object(campaign, "_valid_pair", side_effect=valid_pair):
                tuning = campaign.run_campaign(
                    root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                    output_root=output, max_pairs=1, partition="tuning", execute_pair=execute_pair,
                )
                self.assertEqual(tuning["status"], "pass")
                with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "requires an exact completed tuning-baseline freeze"):
                    campaign.run_campaign(
                        root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                        output_root=output, max_pairs=1, partition="held-out", execute_pair=execute_pair,
                    )
                freeze = campaign.run_campaign(
                    root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                    output_root=output, max_pairs=1, partition="tuning", freeze_tuning=True,
                    execute_pair=execute_pair,
                )
                self.assertEqual(freeze["operation"], "pixel-portal-outcome-tuning-baseline-freeze")
                self.assertEqual(freeze["heldOutTaskCount"], 1)
                self.assertFalse(freeze["heldOutTaskBytesOpened"])
                campaign.evaluation.valid_hash(freeze["heldOutTaskSetSha256"], "held-out commitment")
                self.assertTrue((output / campaign.FREEZE_FILE).is_file())
                with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "retuning requires a new campaign identity"):
                    campaign.run_campaign(
                        root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                        output_root=output, max_pairs=1, partition="tuning", execute_pair=execute_pair,
                    )
                heldout = campaign.run_campaign(
                    root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                    output_root=output, max_pairs=1, partition="held-out", execute_pair=execute_pair,
                )
                self.assertEqual(heldout["status"], "pass")
                self.assertEqual(heldout["partition"], "held-out")
                self.assertTrue(heldout["tuningBaselineFrozen"])
                campaign.evaluation.valid_hash(heldout["tuningBaselineFreezeSha256"], "held-out freeze binding")
                with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "after held-out evidence exists"):
                    campaign.run_campaign(
                        root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                        output_root=output, max_pairs=1, partition="tuning", freeze_tuning=True,
                        execute_pair=execute_pair,
                    )

    def test_blocked_tuning_pair_cannot_freeze_or_disclose_heldout_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            if os.name != "nt":
                parent.chmod(0o700)
            materialization, config, tasks = self.fixture(parent, heldout_focus=True)
            output = parent / "campaign"
            heldout_task = next(
                task for task in tasks
                if task.get("partition", task.get("rehearsal", {}).get("partition", "tuning")) == "held-out"
            )
            heldout_path = materialization / heldout_task["taskId"] / "task.json"
            heldout_path.write_bytes(b"held-out bytes must remain unopened before a passing freeze\n")
            if os.name != "nt":
                heldout_path.chmod(0o600)
            completed = {}

            def execute_pair(**options):
                attempt = options["output_root"]
                campaign.pair_runner._new_private_directory(attempt)
                private_json(attempt / "pair.json", {})
                value = {
                    "schemaVersion": 1, "operation": "pixel-portal-outcome-pair-execution",
                    "taskSha256": "1" * 64, "taskAdmissionSha256": "2" * 64,
                    "configurationSha256": "7" * 64, "preflightSha256": "8" * 64,
                    "modelContractSha256": "9" * 64, "inferenceContractSha256": "a" * 64,
                    "runtimeCondition": options["runtime_condition"], "armOrder": ["pixel", "codex"],
                    "pixelRunSha256": "3" * 64, "codexRunSha256": "4" * 64,
                    "comparisonSha256": "5" * 64, "status": "blocked", "classification": "parity",
                    "boundary": campaign.pair_runner.PAIR_BOUNDARY,
                }
                completed[attempt] = value
                return value

            def valid_pair(_root, _task_path, attempt, expected_order, _expected_bindings, expected_runtime_condition):
                value = completed.get(attempt)
                if value is not None:
                    self.assertEqual(list(expected_order), ["pixel", "codex"])
                    self.assertEqual(value["runtimeCondition"], expected_runtime_condition)
                return value

            with mock.patch.object(campaign, "_valid_pair", side_effect=valid_pair):
                tuning = campaign.run_campaign(
                    root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                    output_root=output, max_pairs=1, partition="tuning", execute_pair=execute_pair,
                )
                self.assertEqual(tuning["status"], "blocked")
                with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "not pass"):
                    campaign.run_campaign(
                        root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                        output_root=output, max_pairs=1, partition="tuning", freeze_tuning=True,
                        execute_pair=execute_pair,
                    )
                self.assertFalse((output / campaign.FREEZE_FILE).exists(), "a blocked tuning baseline must never freeze")
                with self.assertRaisesRegex(campaign.evaluation.OutcomeError, "not pass"):
                    campaign.run_campaign(
                        root=ROOT, materialization_root=materialization, pair_configuration_path=config,
                        output_root=output, max_pairs=1, partition="held-out", execute_pair=execute_pair,
                    )
            self.assertEqual(
                {path.parent.name for path in self.compatibility_calls}, {tasks[0]["taskId"]},
                "held-out task bytes must not be compatibility-reviewed or opened before a passing freeze",
            )
            self.assertEqual(
                heldout_path.read_bytes(), b"held-out bytes must remain unopened before a passing freeze\n",
            )

    def _dispatch(self, argv):
        captured = {}

        def fake_run_campaign(**kwargs):
            captured.update(kwargs)
            return {"operation": "pixel-portal-outcome-battery-campaign-progress", "status": "pass"}

        with mock.patch.object(campaign, "run_campaign", side_effect=fake_run_campaign) as runner, \
                mock.patch.object(sys, "argv", ["portal_outcome_battery_campaign.py"] + argv), \
                mock.patch("builtins.print"):
            code = campaign.main()
        return code, captured, runner

    def _base_argv(self, extra):
        return ["--materialization", "materialized", "--pair-config", "pair.json",
                "--output", "campaign", "--runtime-condition", "cold-first-request"] + extra

    def test_main_legacy_combined_modes_remain_explicit_and_valid(self):
        for partition in ("tuning", "held-out"):
            code, captured, runner = self._dispatch(self._base_argv(["--partition", partition]))
            self.assertEqual(code, 0)
            self.assertEqual(runner.call_count, 1)
            self.assertIsNone(captured["sealed_tuning_root"])
            self.assertIsNone(captured["sealed_reveal_receipt"])
            self.assertIsNone(captured["sealed_tuning_materialization_root"])
            self.assertIsNone(captured["sealed_tuning_output_root"])

    def test_main_sealed_tuning_dispatch_forwarding_and_canonical_paths(self):
        code, captured, runner = self._dispatch(self._base_argv(
            ["--partition", "tuning", "--sealed-tuning-root", "tuning-root"],
        ))
        self.assertEqual(code, 0)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(captured["sealed_tuning_root"], Path(os.path.abspath("tuning-root")))
        self.assertIsNone(captured["sealed_reveal_receipt"])
        self.assertIsNone(captured["sealed_tuning_materialization_root"])
        self.assertIsNone(captured["sealed_tuning_output_root"])

    def test_main_sealed_heldout_dispatch_all_four_evidence_paths(self):
        argv = self._base_argv([
            "--partition", "held-out",
            "--sealed-tuning-root", "tuning-root",
            "--sealed-reveal-receipt", "receipt.json",
            "--sealed-tuning-materialization-root", "tune-mat",
            "--sealed-tuning-output-root", "tune-out",
        ])
        code, captured, runner = self._dispatch(argv)
        self.assertEqual(code, 0)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(captured["sealed_tuning_root"], Path(os.path.abspath("tuning-root")))
        self.assertEqual(captured["sealed_reveal_receipt"], Path(os.path.abspath("receipt.json")))
        self.assertEqual(captured["sealed_tuning_materialization_root"], Path(os.path.abspath("tune-mat")))
        self.assertEqual(captured["sealed_tuning_output_root"], Path(os.path.abspath("tune-out")))

    def test_main_invalid_combinations_fail_before_campaign_invocation(self):
        cases = [
            (["--partition", "tuning", "--sealed-tuning-root", "r",
              "--sealed-reveal-receipt", "receipt"], "forbidden for tuning"),
            (["--partition", "tuning", "--sealed-tuning-root", "r",
              "--sealed-tuning-materialization-root", "mat"], "forbidden for tuning"),
            (["--partition", "tuning", "--sealed-tuning-root", "r",
              "--sealed-tuning-output-root", "out"], "forbidden for tuning"),
            (["--partition", "tuning", "--sealed-reveal-receipt", "receipt"],
             "require a sealed tuning root"),
            (["--partition", "held-out", "--sealed-reveal-receipt", "receipt"],
             "require a sealed tuning root"),
            (["--partition", "held-out", "--sealed-tuning-root", "r",
              "--sealed-reveal-receipt", "receipt",
              "--sealed-tuning-materialization-root", "mat"],
             "original tuning materialization and output"),
            (["--partition", "held-out", "--sealed-tuning-root", "r",
              "--sealed-reveal-receipt", "receipt",
              "--sealed-tuning-output-root", "out"],
             "original tuning materialization and output"),
            (["--partition", "held-out", "--sealed-tuning-root", "r",
              "--sealed-tuning-materialization-root", "mat",
              "--sealed-tuning-output-root", "out"],
             "sealed reveal receipt and original tuning"),
            (["--partition", "held-out", "--freeze-tuning"],
             "valid only for the tuning partition"),
        ]
        for extra, _regex in cases:
            code, _captured, runner = self._dispatch(self._base_argv(extra))
            self.assertEqual(code, 2, f"case {extra}")
            self.assertEqual(runner.call_count, 0, f"campaign invoked for {extra}")

    def test_cli_treats_a_valid_tuning_freeze_as_success(self):
        freeze = {
            "schemaVersion": 1,
            "operation": "pixel-portal-outcome-tuning-baseline-freeze",
            "campaignId": "1" * 64,
        }
        argv = [
            "portal_outcome_battery_campaign.py", "--materialization", "materialized",
            "--pair-config", "pair.json", "--output", "campaign", "--freeze-tuning",
            "--runtime-condition", "cold-first-request",
        ]
        with mock.patch.object(campaign, "run_campaign", return_value=freeze), \
                mock.patch.object(sys, "argv", argv), \
                mock.patch("builtins.print") as printed:
            self.assertEqual(campaign.main(), 0)
        printed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
