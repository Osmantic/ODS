from __future__ import annotations

import ast
import errno
import json
import os
import stat
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock


BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_artifact_stage_store as staging  # noqa: E402
from extension_artifact_verifier import (  # noqa: E402
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

TXN_ID = "txn-" + "a" * 24
PLAN_HASH = "b" * 64


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


def verified_file(content: bytes, relative_path: str, *, inode: int = 1):
    return VerifiedArtifactFile(
        relative_path=relative_path,
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
        manifest=verified_file(manifest, "manifest.yaml"),
        compose=(
            verified_file(compose, definition.compose_file or "compose.yaml", inode=2)
            if compose is not None
            else None
        ),
    )


def bound_command(
    definitions: tuple[PlannedDefinition, ...],
    *,
    actions: tuple[str, ...] | None = None,
    operation_key: str = "stage",
) -> LifecycleWorkCommand:
    actions = actions or tuple("install" for _ in definitions)
    operations = tuple(
        PlannedOperation(definition.service_id, action)
        for definition, action in zip(definitions, actions, strict=True)
    )
    service_ids = tuple(
        operation.service_id for operation in operations if operation.action != "noop"
    )
    material = LifecyclePlanMaterial(
        schema=PLAN_MATERIAL_SCHEMA,
        transaction_id=TXN_ID,
        plan_hash=PLAN_HASH,
        state="staged",
        operations=operations,
        definitions=definitions,
    )
    return LifecycleWorkCommand(
        transaction_id=TXN_ID,
        plan_hash=PLAN_HASH,
        operation_key=operation_key,
        request_hash="c" * 64,
        service_ids=service_ids,
        payload={
            "operations": [
                {"serviceId": operation.service_id, "action": operation.action}
                for operation in operations
                if operation.action != "noop"
            ]
        },
        timeout_seconds=600,
        plan_material=material,
    )


@unittest.skipUnless(SUPPORTED, "requires Linux dir_fd and hard-link semantics")
class ArtifactStageStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "stage"
        self.root.mkdir(mode=0o700)
        self.store = staging.ArtifactStageStore(self.root)

    def assert_code(self, code: str, call):
        with self.assertRaises(staging.ArtifactStageError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)
        return raised.exception

    def final_file(self) -> Path:
        files = list(self.root.glob("[0-9a-f]*.artifact-stage"))
        self.assertEqual(len(files), 1)
        return files[0]

    def stage_one(
        self,
        manifest: bytes = b"name: demo\n",
        *,
        compose: bytes | None = None,
        compose_file: str | None = None,
    ):
        definition = planned_definition(
            "demo",
            manifest,
            compose=compose,
            compose_file=compose_file,
        )
        command = bound_command((definition,))
        verified = verified_definition(definition, manifest, compose=compose)
        return command, verified, self.store.stage(command, (verified,))

    def test_manifest_stage_read_and_idempotent_replay(self) -> None:
        command, _verified, result = self.stage_one()
        self.assertFalse(result.duplicate)
        self.assertEqual(result.transaction_id, TXN_ID)
        self.assertEqual(result.plan_hash, PLAN_HASH)
        self.assertEqual(result.service_ids, ("demo",))
        self.assertEqual(result.definitions[0].manifest.content, b"name: demo\n")
        self.assertRegex(result.bundle_sha256, r"^[0-9a-f]{64}$")
        final = self.final_file()
        self.assertEqual(stat.S_IMODE(final.stat().st_mode), 0o400)
        self.assertEqual(final.stat().st_nlink, 1)

        reread = self.store.read(TXN_ID, PLAN_HASH, ("demo",))
        self.assertEqual(reread.bundle_sha256, result.bundle_sha256)
        self.assertFalse(reread.duplicate)
        duplicate = self.store.stage(command, (_verified,))
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.bundle_sha256, result.bundle_sha256)

    def test_duplicate_replay_retries_failed_directory_durability(self) -> None:
        manifest = b"name: demo\n"
        definition = planned_definition("demo", manifest)
        command = bound_command((definition,))
        verified = verified_definition(definition, manifest)
        real_fsync = os.fsync

        def reject_directory_sync(descriptor: int) -> None:
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError(errno.EIO, "directory sync failed")
            real_fsync(descriptor)

        with mock.patch.object(
            staging.os, "fsync", side_effect=reject_directory_sync
        ):
            self.assert_code(
                "artifact-stage-io-error",
                lambda: self.store.stage(command, (verified,)),
            )
            self.final_file()
            self.assert_code(
                "artifact-stage-io-error",
                lambda: self.store.stage(command, (verified,)),
            )

        duplicate = self.store.stage(command, (verified,))
        self.assertTrue(duplicate.duplicate)

    def test_ordered_batch_nested_compose_and_noop_exclusion(self) -> None:
        first_manifest = b"name: first\n"
        second_manifest = b"name: second\n"
        compose = b"services: {}\n"
        first = planned_definition("first", first_manifest)
        ignored = planned_definition("ignored", b"name: ignored\n")
        second = planned_definition(
            "second",
            second_manifest,
            source="user",
            compose=compose,
            compose_file="nested/compose.yaml",
        )
        command = bound_command(
            (first, ignored, second), actions=("install", "noop", "update")
        )
        result = self.store.stage(
            command,
            (
                verified_definition(first, first_manifest),
                verified_definition(second, second_manifest, compose=compose),
            ),
        )
        self.assertEqual(result.service_ids, ("first", "second"))
        self.assertEqual(
            tuple(item.service_id for item in result.definitions),
            ("first", "second"),
        )
        self.assertEqual(result.definitions[1].definition_source, "user")
        self.assertEqual(
            result.definitions[1].compose.relative_path,
            "nested/compose.yaml",
        )
        self.assertEqual(result.definitions[1].compose.content, compose)

    def test_staging_uses_verified_bytes_not_changed_source(self) -> None:
        source = self.base / "manifest.yaml"
        original = b"name: original\n"
        source.write_bytes(original)
        definition = planned_definition("demo", original)
        verified = verified_definition(definition, original)
        source.write_bytes(b"name: changed\n")
        result = self.store.stage(bound_command((definition,)), (verified,))
        self.assertEqual(result.definitions[0].manifest.content, original)

    def test_divergent_raw_spelling_with_same_semantics_conflicts(self) -> None:
        yaml_bytes = b"name: demo\n"
        json_bytes = b'{"name":"demo"}\n'
        self.assertEqual(
            canonical_document_sha256(yaml_bytes),
            canonical_document_sha256(json_bytes),
        )
        definition = planned_definition("demo", yaml_bytes)
        command = bound_command((definition,))
        self.store.stage(command, (verified_definition(definition, yaml_bytes),))
        divergent = verified_definition(definition, json_bytes)
        self.assert_code(
            "artifact-stage-conflict",
            lambda: self.store.stage(command, (divergent,)),
        )

    def test_invalid_binding_fails_before_store_open(self) -> None:
        manifest = b"name: demo\n"
        definition = planned_definition("demo", manifest)
        verified = verified_definition(definition, manifest)
        command = bound_command((definition,))
        cases = [
            replace(command, operation_key="apply:demo"),
            replace(command, plan_material=None),
            replace(
                command,
                plan_material=replace(command.plan_material, state="downloading"),
            ),
            replace(command, service_ids=("different",)),
        ]
        for candidate in cases:
            with self.subTest(operation=candidate.operation_key):
                with mock.patch.object(staging.os, "open") as opened:
                    self.assert_code(
                        "artifact-stage-binding-invalid",
                        lambda candidate=candidate: self.store.stage(
                            candidate, (verified,)
                        ),
                    )
                    opened.assert_not_called()

    def test_artifact_origin_path_digest_and_shape_mismatch_fail_closed(self) -> None:
        manifest = b"name: demo\n"
        definition = planned_definition("demo", manifest)
        command = bound_command((definition,))
        verified = verified_definition(definition, manifest)
        cases = [
            replace(verified, definition_source="builtin"),
            replace(verified, service_id="other"),
            replace(
                verified,
                manifest=replace(verified.manifest, relative_path="other.yaml"),
            ),
            replace(
                verified,
                manifest=replace(verified.manifest, semantic_sha256="sha256:" + "0" * 64),
            ),
            replace(
                verified,
                manifest=replace(verified.manifest, size=len(manifest) + 1),
            ),
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assert_code(
                    "artifact-stage-binding-invalid",
                    lambda candidate=candidate: self.store.stage(command, (candidate,)),
                )

    def test_unhashable_source_and_action_values_reject_stably(self) -> None:
        manifest = b"name: demo\n"
        definition = planned_definition("demo", manifest)
        verified = verified_definition(definition, manifest)
        command = bound_command((definition,))
        bad_definition = replace(definition, definition_source=["library"])
        bad_operation = replace(
            command.plan_material.operations[0], action=["install"]
        )
        cases = (
            replace(
                command,
                plan_material=replace(
                    command.plan_material, definitions=(bad_definition,)
                ),
            ),
            replace(
                command,
                plan_material=replace(
                    command.plan_material, operations=(bad_operation,)
                ),
            ),
        )
        for candidate in cases:
            self.assert_code(
                "artifact-stage-binding-invalid",
                lambda candidate=candidate: self.store.stage(candidate, (verified,)),
            )

        self.store.stage(command, (verified,))
        final = self.final_file()
        raw = final.read_bytes()
        header_start = len(staging.MAGIC) + 4
        header_size = int.from_bytes(
            raw[len(staging.MAGIC) : header_start], "big"
        )
        header_end = header_start + header_size
        header = json.loads(raw[header_start:header_end])
        header["definitions"][0]["definitionSource"] = ["library"]
        encoded = staging._canonical_json(header)
        tampered = (
            staging.MAGIC
            + len(encoded).to_bytes(4, "big")
            + encoded
            + raw[header_end:]
        )
        final.chmod(0o600)
        final.write_bytes(tampered)
        final.chmod(0o400)
        self.assert_code(
            "artifact-stage-integrity",
            lambda: self.store.read(TXN_ID, PLAN_HASH, ("demo",)),
        )

    def test_root_must_be_absolute_real_and_private(self) -> None:
        command, verified, _result = self.stage_one()
        relative = staging.ArtifactStageStore("relative")
        self.assert_code(
            "artifact-stage-root-invalid",
            lambda: relative.stage(command, (verified,)),
        )
        missing = staging.ArtifactStageStore(self.base / "missing")
        self.assert_code(
            "artifact-stage-root-missing",
            lambda: missing.stage(command, (verified,)),
        )
        unsafe = self.base / "unsafe"
        unsafe.mkdir(mode=0o700)
        unsafe.chmod(0o755)
        self.assert_code(
            "artifact-stage-custody-violation",
            lambda: staging.ArtifactStageStore(unsafe).stage(command, (verified,)),
        )
        real = self.base / "real"
        real.mkdir(mode=0o700)
        linked = self.base / "linked"
        linked.symlink_to(real, target_is_directory=True)
        self.assert_code(
            "artifact-stage-root-invalid",
            lambda: staging.ArtifactStageStore(linked).stage(command, (verified,)),
        )

    def test_symlink_in_root_parent_is_rejected(self) -> None:
        command, verified, _result = self.stage_one()
        real_parent = self.base / "real-parent"
        real_parent.mkdir(mode=0o700)
        nested = real_parent / "nested"
        nested.mkdir(mode=0o700)
        linked_parent = self.base / "parent-link"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        store = staging.ArtifactStageStore(linked_parent / "nested")
        self.assert_code(
            "artifact-stage-root-invalid",
            lambda: store.stage(command, (verified,)),
        )

    def test_published_symlink_hardlink_special_and_writable_are_rejected(self) -> None:
        attacks = ("symlink", "hardlink", "fifo", "writable")
        for attack in attacks:
            with self.subTest(attack=attack):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary) / "stage"
                    root.mkdir(mode=0o700)
                    store = staging.ArtifactStageStore(root)
                    manifest = b"name: demo\n"
                    definition = planned_definition("demo", manifest)
                    command = bound_command((definition,))
                    verified = verified_definition(definition, manifest)
                    store.stage(command, (verified,))
                    final = next(root.glob("[0-9a-f]*.artifact-stage"))
                    if attack == "symlink":
                        final.unlink()
                        target = root / "target"
                        target.write_bytes(b"not a bundle")
                        final.symlink_to(target)
                    elif attack == "hardlink":
                        os.link(final, root / "other")
                    elif attack == "fifo":
                        final.unlink()
                        os.mkfifo(final, 0o400)
                    else:
                        final.chmod(0o600)
                    self.assert_code(
                        (
                            "artifact-stage-custody-violation"
                            if attack == "writable"
                            else "artifact-stage-integrity"
                        ),
                        lambda store=store: store.read(
                            TXN_ID, PLAN_HASH, ("demo",)
                        ),
                    )

    def test_corrupt_noncanonical_truncated_and_oversized_bundles_are_rejected(
        self,
    ) -> None:
        mutations = ("corrupt", "header", "truncate", "oversize")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary) / "stage"
                    root.mkdir(mode=0o700)
                    store = staging.ArtifactStageStore(root)
                    manifest = b"name: demo\n"
                    definition = planned_definition("demo", manifest)
                    store.stage(
                        bound_command((definition,)),
                        (verified_definition(definition, manifest),),
                    )
                    final = next(root.glob("[0-9a-f]*.artifact-stage"))
                    final.chmod(0o600)
                    if mutation == "corrupt":
                        raw = bytearray(final.read_bytes())
                        raw[-1] ^= 1
                        final.write_bytes(raw)
                    elif mutation == "header":
                        raw = bytearray(final.read_bytes())
                        header_size = int.from_bytes(
                            raw[len(staging.MAGIC) : len(staging.MAGIC) + 4],
                            "big",
                        )
                        header_end = len(staging.MAGIC) + 4 + header_size
                        raw[header_end - 1] = ord(" ")
                        final.write_bytes(raw)
                    elif mutation == "truncate":
                        final.write_bytes(final.read_bytes()[:10])
                    else:
                        with final.open("r+b") as stream:
                            stream.truncate(staging.MAX_STAGE_BUNDLE_BYTES + 1)
                    final.chmod(0o400)
                    self.assert_code(
                        (
                            "artifact-stage-size-exceeded"
                            if mutation == "oversize"
                            else "artifact-stage-integrity"
                        ),
                        lambda store=store: store.read(
                            TXN_ID, PLAN_HASH, ("demo",)
                        ),
                    )

    def test_in_place_mutation_during_replay_is_rejected(self) -> None:
        self.stage_one()
        final = self.final_file()
        original = final.read_bytes()
        real_read = os.read
        changed = False

        def drifting_read(descriptor: int, size: int) -> bytes:
            nonlocal changed
            result = real_read(descriptor, size)
            if result and not changed:
                changed = True
                final.chmod(0o600)
                final.write_bytes(original + b"changed")
                final.chmod(0o400)
            return result

        with mock.patch.object(staging.os, "read", side_effect=drifting_read):
            self.assert_code(
                "artifact-stage-integrity",
                lambda: self.store.read(TXN_ID, PLAN_HASH, ("demo",)),
            )

    def test_partial_writes_complete_and_zero_write_fails(self) -> None:
        manifest = b"name: demo\n"
        definition = planned_definition("demo", manifest)
        command = bound_command((definition,))
        verified = verified_definition(definition, manifest)
        real_write = os.write

        def partial(descriptor: int, content: bytes) -> int:
            return real_write(descriptor, content[:7])

        with mock.patch.object(staging.os, "write", side_effect=partial):
            result = self.store.stage(command, (verified,))
        self.assertEqual(result.definitions[0].manifest.content, manifest)

        other_root = self.base / "zero"
        other_root.mkdir(mode=0o700)
        with mock.patch.object(staging.os, "write", return_value=0):
            self.assert_code(
                "artifact-stage-io-error",
                lambda: staging.ArtifactStageStore(other_root).stage(
                    command, (verified,)
                ),
            )
        self.assertEqual(list(other_root.iterdir()), [])

    def test_link_unsupported_fails_without_fallback(self) -> None:
        manifest = b"name: demo\n"
        definition = planned_definition("demo", manifest)
        command = bound_command((definition,))
        verified = verified_definition(definition, manifest)
        with (
            mock.patch.object(staging, "_validate_platform"),
            mock.patch.object(
                staging.os,
                "link",
                side_effect=OSError(errno.EOPNOTSUPP, "unsupported"),
            ),
        ):
            self.assert_code(
                "artifact-stage-link-unsupported",
                lambda: self.store.stage(command, (verified,)),
            )
        self.assertEqual(list(self.root.iterdir()), [])

    def test_substituted_temp_is_not_unlinked(self) -> None:
        manifest = b"name: demo\n"
        definition = planned_definition("demo", manifest)
        command = bound_command((definition,))
        verified = verified_definition(definition, manifest)
        self.store.stage(command, (verified,))
        real_unlink = os.unlink
        real_open = os.open
        real_write = os.write

        def substitute(
            source: str,
            _destination: str,
            *,
            src_dir_fd: int,
            dst_dir_fd: int,
            follow_symlinks: bool,
        ) -> None:
            self.assertEqual(src_dir_fd, dst_dir_fd)
            self.assertFalse(follow_symlinks)
            real_unlink(source, dir_fd=src_dir_fd)
            descriptor = real_open(
                source,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=src_dir_fd,
            )
            try:
                real_write(descriptor, b"attacker-substitute")
            finally:
                os.close(descriptor)
            raise FileExistsError(errno.EEXIST, "winner exists")

        with (
            mock.patch.object(staging, "_validate_platform"),
            mock.patch.object(staging.os, "link", side_effect=substitute),
        ):
            self.assert_code(
                "artifact-stage-temp-integrity",
                lambda: self.store.stage(command, (verified,)),
            )
        leftovers = list(self.root.glob("tmp-*.artifact-stage"))
        self.assertEqual(len(leftovers), 1)
        self.assertEqual(leftovers[0].read_bytes(), b"attacker-substitute")

    def test_bundle_header_is_canonical_and_contains_no_source_path(self) -> None:
        _command, _verified, result = self.stage_one()
        raw = self.final_file().read_bytes()
        header_start = len(staging.MAGIC) + 4
        header_size = int.from_bytes(
            raw[len(staging.MAGIC) : header_start], "big"
        )
        header = raw[header_start : header_start + header_size]
        self.assertTrue(header.endswith(b"\n"))
        self.assertNotIn(str(self.base).encode(), raw)
        self.assertNotIn(b"created", header.lower())
        self.assertNotIn(b"timestamp", header.lower())
        self.assertEqual(
            result.bundle_sha256,
            __import__("hashlib").sha256(raw).hexdigest(),
        )

    def test_module_has_no_service_effect_discovery_or_overwrite_primitive(self) -> None:
        source_path = BIN_DIR / "extension_artifact_stage_store.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imports = {
            alias.name.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertTrue(
            imports.isdisjoint({"subprocess", "socket", "urllib", "requests"})
        )
        forbidden_os_calls = {
            "chmod",
            "listdir",
            "makedirs",
            "mkdir",
            "remove",
            "rename",
            "replace",
            "scandir",
            "system",
            "walk",
        }
        used_os_calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
        }
        self.assertTrue(used_os_calls.isdisjoint(forbidden_os_calls))


if __name__ == "__main__":
    unittest.main()
