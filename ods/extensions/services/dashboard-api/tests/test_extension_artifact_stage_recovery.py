from __future__ import annotations

import ast
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock


BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_artifact_stage_recovery as recovery  # noqa: E402
import extension_artifact_stage_store as staging  # noqa: E402
from extension_lifecycle_work import LifecycleWorkCommand  # noqa: E402


TXN_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
REQUEST_HASH = "3" * 64
BUNDLE_HASH = "4" * 64


def command(operation_key: str = "stage") -> LifecycleWorkCommand:
    return LifecycleWorkCommand(
        transaction_id=TXN_ID,
        plan_hash=PLAN_HASH,
        operation_key=operation_key,
        request_hash=REQUEST_HASH,
        service_ids=("demo",),
        payload={"operations": []},
        timeout_seconds=600,
    )


def staged_batch() -> staging.StagedArtifactBatch:
    manifest = staging.StagedArtifactFile(
        relative_path="manifest.yaml",
        content=b"name: demo\n",
        semantic_sha256="5" * 64,
        raw_sha256="6" * 64,
        size=11,
    )
    definition = staging.StagedDefinitionArtifacts(
        service_id="demo",
        definition_source="library",
        manifest=manifest,
        compose=None,
    )
    return staging.StagedArtifactBatch(
        transaction_id=TXN_ID,
        plan_hash=PLAN_HASH,
        service_ids=("demo",),
        definitions=(definition,),
        bundle_sha256=BUNDLE_HASH,
        duplicate=False,
    )


class ArtifactStageRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = staging.ArtifactStageStore("/not-opened/stage")
        self.observer = recovery.ArtifactStageRecoveryObserver(self.store)

    def assert_code(self, code: str, call) -> BaseException:
        with self.assertRaises(Exception) as raised:
            call()
        self.assertEqual(getattr(raised.exception, "code", None), code)
        self.assertEqual(str(raised.exception), code)
        return raised.exception

    def test_exact_bundle_returns_typed_completed_observation(self) -> None:
        value = command()
        with mock.patch.object(
            self.store, "read", return_value=staged_batch()
        ) as read:
            observed = self.observer(value)

        self.assertEqual(observed.state, "completed")
        self.assertEqual(observed.evidence_hash, BUNDLE_HASH)
        read.assert_called_once_with(TXN_ID, PLAN_HASH, ("demo",))

    def test_missing_bundle_is_the_only_dispatchable_observation(self) -> None:
        missing = staging.ArtifactStageError(
            "artifact-stage-missing", field="private-path"
        )
        with mock.patch.object(self.store, "read", side_effect=missing):
            observed = self.observer(command())

        self.assertEqual(observed.state, "missing")
        self.assertIsNone(observed.evidence_hash)

    def test_store_integrity_and_runtime_failures_are_value_safe(self) -> None:
        failures = (
            staging.ArtifactStageError(
                "artifact-stage-integrity", field="private-path"
            ),
            OSError("private-runtime-detail"),
        )
        for failure in failures:
            with self.subTest(failure=failure):
                with mock.patch.object(self.store, "read", side_effect=failure):
                    error = self.assert_code(
                        "lifecycle-work-artifact-stage-observation-failed",
                        lambda: self.observer(command()),
                    )
                self.assertNotIn("private", str(error))

    def test_invalid_post_read_batch_fails_closed(self) -> None:
        invalid = (
            object(),
            replace(staged_batch(), transaction_id="txn-" + "9" * 24),
            replace(staged_batch(), plan_hash="8" * 64),
            replace(staged_batch(), service_ids=("different",)),
            replace(staged_batch(), bundle_sha256="not-a-hash"),
        )
        for batch in invalid:
            with self.subTest(batch=batch):
                with mock.patch.object(self.store, "read", return_value=batch):
                    self.assert_code(
                        "lifecycle-work-artifact-stage-observation-failed",
                        lambda: self.observer(command()),
                    )

    def test_rejects_non_stage_commands_and_unreviewed_store_types(self) -> None:
        class DerivedStore(staging.ArtifactStageStore):
            pass

        with mock.patch.object(self.store, "read") as read:
            self.assert_code(
                "lifecycle-work-artifact-stage-observer-invalid",
                lambda: self.observer(command("verify")),
            )
        read.assert_not_called()
        self.assert_code(
            "lifecycle-work-artifact-stage-observer-invalid",
            lambda: recovery.ArtifactStageRecoveryObserver(
                DerivedStore("/not-opened/stage")
            ),
        )

    def test_module_has_no_direct_effect_or_discovery_authority(self) -> None:
        path = BIN_DIR / "extension_artifact_stage_recovery.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertTrue(
            imported.isdisjoint(
                {"os", "pathlib", "shutil", "socket", "subprocess", "urllib"}
            )
        )

    def test_only_dormant_runtime_composition_imports_the_observer(self) -> None:
        repo = Path(__file__).resolve().parents[5]
        observer_path = BIN_DIR / "extension_artifact_stage_recovery.py"
        runtime_path = BIN_DIR / "extension_artifact_stage_runtime.py"
        hits: list[str] = []
        for path in (repo / "ods").rglob("*.py"):
            if path in {observer_path, runtime_path} or "tests" in path.parts:
                continue
            if "extension_artifact_stage_recovery" in path.read_text(
                encoding="utf-8"
            ):
                hits.append(str(path.relative_to(repo)))
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
