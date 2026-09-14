from __future__ import annotations

import ast
import hashlib
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock


BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_artifact_stage_adapter as adapter  # noqa: E402
import extension_artifact_stage_store as staging  # noqa: E402
from extension_artifact_verifier import (  # noqa: E402
    HostArtifactError,
    HostArtifactRoots,
    VerifiedArtifactFile,
    VerifiedDefinitionArtifacts,
)
from extension_document_digest import canonical_document_sha256  # noqa: E402
from extension_lifecycle_plan import (  # noqa: E402
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedOperation,
)
from extension_lifecycle_work import LifecycleWorkCommand  # noqa: E402


SUPPORTED = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.link in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)

TXN_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
BUNDLE_HASH = "3" * 64


def planned_definition(
    service_id: str,
    manifest: bytes,
    *,
    source: str = "library",
    compose: bytes | None = None,
    compose_file: str | None = None,
) -> PlannedDefinition:
    return PlannedDefinition(
        service_id=service_id,
        service_type="docker",
        manifest_schema_version="ods.services.v2",
        version="1.0.0",
        data_schema_version="1",
        definition_sha256=canonical_document_sha256(manifest),
        compose_sha256=(
            canonical_document_sha256(compose) if compose is not None else None
        ),
        definition_source=source,
        compose_file=compose_file,
        images=(),
        builds=(),
        canonical_document=b"ignored\n",
    )


def bound_command(
    definitions: tuple[PlannedDefinition, ...],
    *,
    actions: tuple[str, ...] | None = None,
) -> LifecycleWorkCommand:
    actions = actions or tuple("install" for _definition in definitions)
    operations = tuple(
        PlannedOperation(definition.service_id, action)
        for definition, action in zip(definitions, actions, strict=True)
    )
    service_ids = tuple(
        operation.service_id for operation in operations if operation.action != "noop"
    )
    return LifecycleWorkCommand(
        transaction_id=TXN_ID,
        plan_hash=PLAN_HASH,
        operation_key="stage",
        request_hash="4" * 64,
        service_ids=service_ids,
        payload={
            "operations": [
                {"serviceId": operation.service_id, "action": operation.action}
                for operation in operations
                if operation.action != "noop"
            ]
        },
        timeout_seconds=600,
        plan_material=LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN_ID,
            plan_hash=PLAN_HASH,
            state="staged",
            operations=operations,
            definitions=definitions,
        ),
    )


def verified_file(content: bytes, path: str, inode: int) -> VerifiedArtifactFile:
    return VerifiedArtifactFile(
        relative_path=path,
        content=content,
        semantic_sha256=canonical_document_sha256(content),
        device=1,
        inode=inode,
        size=len(content),
    )


def verified_definition(
    definition: PlannedDefinition,
    manifest: bytes,
    *,
    compose: bytes | None = None,
) -> VerifiedDefinitionArtifacts:
    return VerifiedDefinitionArtifacts(
        service_id=definition.service_id,
        definition_source=definition.definition_source or "legacy",
        manifest=verified_file(manifest, "manifest.yaml", 1),
        compose=(
            verified_file(compose, definition.compose_file or "compose.yaml", 2)
            if compose is not None
            else None
        ),
    )


def staged_file(content: bytes, path: str) -> staging.StagedArtifactFile:
    return staging.StagedArtifactFile(
        relative_path=path,
        content=content,
        semantic_sha256=canonical_document_sha256(content),
        raw_sha256="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


def staged_batch(
    command: LifecycleWorkCommand,
    definitions: tuple[PlannedDefinition, ...],
    manifests: dict[str, bytes],
    *,
    duplicate: bool = False,
) -> staging.StagedArtifactBatch:
    staged = tuple(
        staging.StagedDefinitionArtifacts(
            service_id=definition.service_id,
            definition_source=definition.definition_source or "legacy",
            manifest=staged_file(manifests[definition.service_id], "manifest.yaml"),
            compose=None,
        )
        for definition in definitions
    )
    return staging.StagedArtifactBatch(
        transaction_id=command.transaction_id,
        plan_hash=command.plan_hash,
        service_ids=command.service_ids,
        definitions=staged,
        bundle_sha256=BUNDLE_HASH,
        duplicate=duplicate,
    )


class ArtifactStageAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.roots = HostArtifactRoots(
            builtin="/not-opened/builtin",
            library="/not-opened/library",
            user="/not-opened/user",
        )
        self.store = staging.ArtifactStageStore("/not-opened/stage")

    def assert_code(self, code: str, call) -> BaseException:
        with self.assertRaises(Exception) as raised:
            call()
        self.assertEqual(getattr(raised.exception, "code", None), code)
        self.assertEqual(str(raised.exception), code)
        return raised.exception

    def test_verifies_complete_mutable_order_before_one_store_call(self) -> None:
        payloads = {
            "first": b"name: first\n",
            "ignored": b"name: ignored\n",
            "second": b"name: second\n",
        }
        first = planned_definition("first", payloads["first"], source="builtin")
        ignored = planned_definition("ignored", payloads["ignored"])
        second = planned_definition("second", payloads["second"], source="user")
        command = bound_command(
            (first, ignored, second), actions=("install", "noop", "update")
        )
        events: list[str] = []

        def verify(
            definition: PlannedDefinition, roots: HostArtifactRoots
        ) -> VerifiedDefinitionArtifacts:
            self.assertIs(roots, self.roots)
            events.append(f"verify:{definition.service_id}")
            return verified_definition(definition, payloads[definition.service_id])

        expected = staged_batch(
            command, (first, second), payloads
        )

        def stage(
            received_command: LifecycleWorkCommand,
            received: tuple[VerifiedDefinitionArtifacts, ...],
        ) -> staging.StagedArtifactBatch:
            events.append("stage")
            self.assertIs(received_command, command)
            self.assertEqual(
                tuple(item.service_id for item in received), ("first", "second")
            )
            return expected

        with (
            mock.patch.object(
                adapter, "verify_planned_definition", side_effect=verify
            ),
            mock.patch.object(self.store, "stage", side_effect=stage) as published,
        ):
            result = adapter.ArtifactStageAdapter(self.roots, self.store)(command)
        self.assertEqual(result, BUNDLE_HASH)
        self.assertEqual(events, ["verify:first", "verify:second", "stage"])
        published.assert_called_once()

    def test_binding_failure_precedes_verifier_and_store(self) -> None:
        definition = planned_definition("demo", b"name: demo\n")
        command = bound_command((definition,))
        invalid = (
            replace(command, operation_key="backup"),
            replace(
                command,
                plan_material=replace(command.plan_material, state="applying"),
            ),
            replace(command, service_ids=("different",)),
        )
        for value in invalid:
            with self.subTest(command=value):
                verify = mock.Mock()
                with (
                    mock.patch.object(adapter, "verify_planned_definition", verify),
                    mock.patch.object(self.store, "stage") as published,
                ):
                    self.assert_code(
                        "lifecycle-work-plan-mismatch",
                        lambda value=value: adapter.ArtifactStageAdapter(
                            self.roots, self.store
                        )(value),
                    )
                verify.assert_not_called()
                published.assert_not_called()

    def test_verification_failure_prevents_any_publication(self) -> None:
        payloads = {
            "first": b"name: first\n",
            "second": b"name: second\n",
            "third": b"name: third\n",
        }
        definitions = tuple(
            planned_definition(service_id, payload)
            for service_id, payload in payloads.items()
        )
        command = bound_command(definitions)
        calls: list[str] = []

        def verify(
            definition: PlannedDefinition, _roots: HostArtifactRoots
        ) -> VerifiedDefinitionArtifacts:
            calls.append(definition.service_id)
            if definition.service_id == "second":
                raise HostArtifactError(
                    "artifact-digest-mismatch", field="private-path"
                )
            return verified_definition(definition, payloads[definition.service_id])

        with (
            mock.patch.object(
                adapter, "verify_planned_definition", side_effect=verify
            ),
            mock.patch.object(self.store, "stage") as published,
        ):
            error = self.assert_code(
                "lifecycle-work-artifact-verification-failed",
                lambda: adapter.ArtifactStageAdapter(self.roots, self.store)(command),
            )
        self.assertEqual(calls, ["first", "second"])
        self.assertNotIn("private-path", str(error))
        published.assert_not_called()

    def test_invalid_verifier_result_and_store_failure_are_stable(self) -> None:
        manifest = b"name: demo\n"
        definition = planned_definition("demo", manifest)
        command = bound_command((definition,))
        with (
            mock.patch.object(
                adapter, "verify_planned_definition", return_value=object()
            ),
            mock.patch.object(self.store, "stage") as published,
        ):
            self.assert_code(
                "lifecycle-work-artifact-verification-failed",
                lambda: adapter.ArtifactStageAdapter(self.roots, self.store)(command),
            )
        published.assert_not_called()

        verified = verified_definition(definition, manifest)
        with (
            mock.patch.object(
                adapter, "verify_planned_definition", return_value=verified
            ),
            mock.patch.object(
                self.store,
                "stage",
                side_effect=staging.ArtifactStageError(
                    "artifact-stage-io-error", field="private-path"
                ),
            ),
        ):
            error = self.assert_code(
                "lifecycle-work-artifact-stage-failed",
                lambda: adapter.ArtifactStageAdapter(self.roots, self.store)(command),
            )
        self.assertNotIn("private-path", str(error))

    def test_post_write_evidence_mismatch_has_a_distinct_failure(self) -> None:
        manifest = b"name: demo\n"
        definition = planned_definition("demo", manifest)
        command = bound_command((definition,))
        verified = verified_definition(definition, manifest)
        valid = staged_batch(command, (definition,), {"demo": manifest})
        invalid = (
            replace(valid, transaction_id="txn-" + "9" * 24),
            replace(valid, plan_hash="8" * 64),
            replace(valid, service_ids=("different",)),
            replace(valid, definitions=()),
            replace(valid, bundle_sha256="not-a-hash"),
            replace(valid, duplicate=1),
        )
        for batch in invalid:
            with self.subTest(batch=batch):
                with (
                    mock.patch.object(
                        adapter,
                        "verify_planned_definition",
                        return_value=verified,
                    ),
                    mock.patch.object(self.store, "stage", return_value=batch),
                ):
                    self.assert_code(
                        "lifecycle-work-artifact-stage-evidence-mismatch",
                        lambda: adapter.ArtifactStageAdapter(
                            self.roots, self.store
                        )(command),
                    )

    def test_constructor_rejects_unreviewed_dependencies(self) -> None:
        class DerivedRoots(HostArtifactRoots):
            pass

        class DerivedStore(staging.ArtifactStageStore):
            pass

        self.assert_code(
            "lifecycle-work-artifact-stage-adapter-invalid",
            lambda: adapter.ArtifactStageAdapter(object(), self.store),  # type: ignore[arg-type]
        )
        self.assert_code(
            "lifecycle-work-artifact-stage-adapter-invalid",
            lambda: adapter.ArtifactStageAdapter(self.roots, object()),  # type: ignore[arg-type]
        )
        self.assert_code(
            "lifecycle-work-artifact-stage-adapter-invalid",
            lambda: adapter.ArtifactStageAdapter(DerivedRoots(), self.store),
        )
        self.assert_code(
            "lifecycle-work-artifact-stage-adapter-invalid",
            lambda: adapter.ArtifactStageAdapter(
                self.roots, DerivedStore("/not-opened/stage")
            ),
        )

    def test_selector_is_pure_and_returns_only_mutable_plan_order(self) -> None:
        first = planned_definition("first", b"name: first\n")
        ignored = planned_definition("ignored", b"name: ignored\n")
        second = planned_definition("second", b"name: second\n")
        command = bound_command(
            (first, ignored, second), actions=("install", "noop", "repair")
        )
        with mock.patch.object(
            staging.os, "open", side_effect=AssertionError("must remain pure")
        ) as opened:
            selected = staging.select_stage_definitions(command)
        self.assertEqual(selected, (first, second))
        opened.assert_not_called()

    def test_module_has_no_direct_effect_or_discovery_authority(self) -> None:
        source_path = BIN_DIR / "extension_artifact_stage_adapter.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
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
        source = source_path.read_text(encoding="utf-8")
        for primitive in (
            "os.open(",
            "open(",
            "glob(",
            "listdir(",
            "scandir(",
            "rename(",
            "replace(",
            "systemctl",
            "docker",
        ):
            self.assertNotIn(primitive, source)

    def test_only_dormant_recovery_observer_imports_the_adapter(self) -> None:
        repo = Path(__file__).resolve().parents[5]
        adapter_path = BIN_DIR / "extension_artifact_stage_adapter.py"
        recovery_path = BIN_DIR / "extension_artifact_stage_recovery.py"
        hits: list[str] = []
        for path in (repo / "ods").rglob("*.py"):
            if path in {adapter_path, recovery_path, Path(__file__).resolve()}:
                continue
            if "extension_artifact_stage_adapter" in path.read_text(
                encoding="utf-8"
            ):
                hits.append(str(path.relative_to(repo)))
        self.assertEqual(hits, [])


@unittest.skipUnless(SUPPORTED, "requires Linux dir_fd and hard-link semantics")
class ArtifactStageAdapterIntegrationTests(unittest.TestCase):
    def test_real_verifier_and_store_publish_one_replayable_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            roots = {
                source: base / source for source in ("builtin", "library", "user")
            }
            for root in roots.values():
                root.mkdir(mode=0o700)
            stage_root = base / "stage"
            stage_root.mkdir(mode=0o700)

            first_manifest = b"name: first\n"
            second_manifest = b"name: second\n"
            second_compose = b"services: {}\n"
            first = planned_definition(
                "first", first_manifest, source="builtin"
            )
            ignored = planned_definition("ignored", b"name: ignored\n")
            second = planned_definition(
                "second",
                second_manifest,
                source="user",
                compose=second_compose,
                compose_file="nested/compose.yaml",
            )
            command = bound_command(
                (first, ignored, second), actions=("install", "noop", "update")
            )

            first_dir = roots["builtin"] / "first"
            first_dir.mkdir(mode=0o700)
            (first_dir / "manifest.yaml").write_bytes(first_manifest)
            second_dir = roots["user"] / "second"
            second_dir.mkdir(mode=0o700)
            (second_dir / "manifest.yaml").write_bytes(second_manifest)
            compose_dir = second_dir / "nested"
            compose_dir.mkdir(mode=0o700)
            (compose_dir / "compose.yaml").write_bytes(second_compose)

            store = staging.ArtifactStageStore(stage_root)
            dispatcher = adapter.ArtifactStageAdapter(
                HostArtifactRoots(**roots), store
            )
            first_hash = dispatcher(command)
            replay_hash = dispatcher(command)
            self.assertEqual(replay_hash, first_hash)
            self.assertRegex(first_hash, r"^[0-9a-f]{64}$")
            self.assertEqual(len(list(stage_root.iterdir())), 1)

            batch = store.read(TXN_ID, PLAN_HASH, ("first", "second"))
            self.assertEqual(batch.bundle_sha256, first_hash)
            self.assertEqual(
                tuple(item.service_id for item in batch.definitions),
                ("first", "second"),
            )
            self.assertEqual(batch.definitions[0].manifest.content, first_manifest)
            self.assertEqual(
                batch.definitions[1].compose.content, second_compose
            )


if __name__ == "__main__":
    unittest.main()
