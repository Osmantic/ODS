from __future__ import annotations

import ast
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

import extension_artifact_verifier as verifier  # noqa: E402
from extension_document_digest import canonical_document_sha256  # noqa: E402
from extension_library_tree_digest import digest_extension_tree  # noqa: E402
from extension_lifecycle_plan import PlannedDefinition  # noqa: E402


SUPPORTED = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
)


def definition(
    manifest: bytes,
    *,
    source: object = "library",
    service_id: object = "demo",
    compose: bytes | None = None,
    compose_file: object = None,
) -> PlannedDefinition:
    return PlannedDefinition(
        service_id=service_id,  # type: ignore[arg-type]
        service_type="docker",
        manifest_schema_version="ods.services.v2",
        version="1.0.0",
        data_schema_version="1",
        definition_sha256=canonical_document_sha256(manifest),
        compose_sha256=(
            canonical_document_sha256(compose) if compose is not None else None
        ),
        definition_source=source,  # type: ignore[arg-type]
        compose_file=compose_file,  # type: ignore[arg-type]
        images=(),
        builds=(),
        canonical_document=b"ignored-plan-document\n",
    )


@unittest.skipUnless(SUPPORTED, "requires Linux dir_fd and O_NOFOLLOW semantics")
class ArtifactVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.roots = {
            source: self.base / source for source in ("builtin", "library", "user")
        }
        for root in self.roots.values():
            root.mkdir(mode=0o700)
        self.injected = verifier.HostArtifactRoots(**self.roots)

    def write(
        self,
        source: str,
        manifest: bytes,
        *,
        service_id: str = "demo",
        compose: bytes | None = None,
        compose_file: str = "compose.yaml",
    ) -> tuple[Path, Path | None]:
        service = self.roots[source] / service_id
        service.mkdir(mode=0o700)
        manifest_path = service / "manifest.yaml"
        manifest_path.write_bytes(manifest)
        manifest_path.chmod(0o600)
        compose_path = None
        if compose is not None:
            compose_path = service / compose_file
            compose_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            compose_path.write_bytes(compose)
            compose_path.chmod(0o600)
        return manifest_path, compose_path

    def assert_code(self, expected: str, action) -> verifier.HostArtifactError:
        with self.assertRaises(verifier.HostArtifactError) as raised:
            action()
        self.assertEqual(raised.exception.code, expected)
        self.assertEqual(str(raised.exception), expected)
        return raised.exception

    def test_all_sources_select_only_the_plan_bound_collision(self) -> None:
        manifests = {
            source: f"name: {source}\n".encode()
            for source in ("builtin", "library", "user")
        }
        for source, payload in manifests.items():
            self.write(source, payload)

        for source, payload in manifests.items():
            with self.subTest(source=source):
                result = verifier.verify_planned_definition(
                    definition(payload, source=source), self.injected
                )
                self.assertEqual(result.definition_source, source)
                self.assertEqual(result.manifest.content, payload)
                self.assertEqual(result.manifest.relative_path, "manifest.yaml")
                self.assertEqual(result.manifest.size, len(payload))
                self.assertIsNone(result.compose)

    def test_compose_returns_the_exact_verified_bytes(self) -> None:
        manifest = b"name: demo\n"
        compose = b"services:\n  app:\n    image: example@sha256:abc\n"
        self.write(
            "library",
            manifest,
            compose=compose,
            compose_file="nested/compose.yaml",
        )
        result = verifier.verify_planned_definition(
            definition(
                manifest,
                compose=compose,
                compose_file="nested/compose.yaml",
            ),
            self.injected,
        )
        self.assertIsNotNone(result.compose)
        assert result.compose is not None
        self.assertEqual(result.compose.content, compose)
        self.assertEqual(result.compose.relative_path, "nested/compose.yaml")

    def test_digest_mismatch_never_falls_back_to_another_root(self) -> None:
        expected = b"name: expected\n"
        self.write("builtin", expected)
        self.write("library", b"name: different\n")
        self.assert_code(
            "artifact-digest-mismatch",
            lambda: verifier.verify_planned_definition(
                definition(expected, source="library"), self.injected
            ),
        )

    def test_legacy_definition_fails_before_any_filesystem_open(self) -> None:
        planned = definition(b"name: demo\n", source=None)
        with mock.patch.object(
            verifier.os, "open", side_effect=AssertionError("must not open")
        ) as opened:
            self.assert_code(
                "artifact-legacy-definition",
                lambda: verifier.verify_planned_definition(planned, self.injected),
            )
        opened.assert_not_called()

    def test_invalid_plan_shapes_fail_closed(self) -> None:
        base = definition(b"name: demo\n")
        changes = [
            ({"definition_source": []}, "artifact-plan-invalid"),
            ({"service_id": "../demo"}, "artifact-plan-invalid"),
            ({"definition_sha256": []}, "artifact-plan-invalid"),
            ({"compose_file": "compose.yaml"}, "artifact-compose-inconsistent"),
            (
                {
                    "compose_file": "../compose.yaml",
                    "compose_sha256": "sha256:" + "0" * 64,
                },
                "artifact-plan-invalid",
            ),
            (
                {"compose_file": "compose.yaml", "compose_sha256": []},
                "artifact-plan-invalid",
            ),
        ]
        for change, code in changes:
            with self.subTest(change=change):
                self.assert_code(
                    code,
                    lambda change=change: verifier.verify_planned_definition(
                        replace(base, **change), self.injected
                    ),
                )

    def test_missing_selected_root_service_manifest_and_compose(self) -> None:
        manifest = b"name: demo\n"
        absent = verifier.HostArtifactRoots(library=self.base / "absent")
        self.assert_code(
            "artifact-root-missing",
            lambda: verifier.verify_planned_definition(definition(manifest), absent),
        )
        self.assert_code(
            "artifact-file-missing",
            lambda: verifier.verify_planned_definition(
                definition(manifest), self.injected
            ),
        )
        service = self.roots["library"] / "demo"
        service.mkdir(mode=0o700)
        self.assert_code(
            "artifact-file-missing",
            lambda: verifier.verify_planned_definition(
                definition(manifest), self.injected
            ),
        )
        manifest_path = service / "manifest.yaml"
        manifest_path.write_bytes(manifest)
        manifest_path.chmod(0o600)
        planned = definition(
            manifest,
            compose=b"services: {}\n",
            compose_file="compose.yaml",
        )
        self.assert_code(
            "artifact-file-missing",
            lambda: verifier.verify_planned_definition(planned, self.injected),
        )

    def test_relative_filesystem_root_and_symlink_root_are_rejected(self) -> None:
        planned = definition(b"name: demo\n")
        for raw in ("relative", Path("/")):
            with self.subTest(root=raw):
                self.assert_code(
                    "artifact-root-invalid",
                    lambda raw=raw: verifier.verify_planned_definition(
                        planned, verifier.HostArtifactRoots(library=raw)
                    ),
                )
        link = self.base / "linked-root"
        link.symlink_to(self.roots["library"], target_is_directory=True)
        self.assert_code(
            "artifact-path-rejected",
            lambda: verifier.verify_planned_definition(
                planned, verifier.HostArtifactRoots(library=link)
            ),
        )

    def test_symlinked_service_manifest_and_nested_compose_are_rejected(self) -> None:
        manifest = b"name: demo\n"
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "manifest.yaml").write_bytes(manifest)
        (self.roots["library"] / "demo").symlink_to(
            outside, target_is_directory=True
        )
        self.assert_code(
            "artifact-path-rejected",
            lambda: verifier.verify_planned_definition(
                definition(manifest), self.injected
            ),
        )

        (self.roots["library"] / "demo").unlink()
        service = self.roots["library"] / "demo"
        service.mkdir(mode=0o700)
        (service / "manifest.yaml").symlink_to(outside / "manifest.yaml")
        self.assert_code(
            "artifact-path-rejected",
            lambda: verifier.verify_planned_definition(
                definition(manifest), self.injected
            ),
        )

        (service / "manifest.yaml").unlink()
        manifest_path = service / "manifest.yaml"
        manifest_path.write_bytes(manifest)
        manifest_path.chmod(0o600)
        nested_target = self.base / "nested-target"
        nested_target.mkdir()
        compose = b"services: {}\n"
        (nested_target / "compose.yaml").write_bytes(compose)
        (service / "nested").symlink_to(nested_target, target_is_directory=True)
        self.assert_code(
            "artifact-path-rejected",
            lambda: verifier.verify_planned_definition(
                definition(
                    manifest,
                    compose=compose,
                    compose_file="nested/compose.yaml",
                ),
                self.injected,
            ),
        )

    def test_hardlink_and_non_regular_files_are_rejected(self) -> None:
        manifest = b"name: demo\n"
        manifest_path, _ = self.write("library", manifest)
        os.link(manifest_path, self.base / "second-link")
        self.assert_code(
            "artifact-hardlink-rejected",
            lambda: verifier.verify_planned_definition(
                definition(manifest), self.injected
            ),
        )

        manifest_path.unlink()
        (self.base / "second-link").unlink()
        manifest_path.mkdir()
        self.assert_code(
            "artifact-not-regular-file",
            lambda: verifier.verify_planned_definition(
                definition(manifest), self.injected
            ),
        )

    def test_fifo_is_rejected_without_blocking(self) -> None:
        manifest = b"name: demo\n"
        service = self.roots["library"] / "demo"
        service.mkdir(mode=0o700)
        os.mkfifo(service / "manifest.yaml")
        self.assert_code(
            "artifact-not-regular-file",
            lambda: verifier.verify_planned_definition(
                definition(manifest), self.injected
            ),
        )

    def test_root_service_nested_directory_and_file_custody_are_enforced(self) -> None:
        manifest = b"name: demo\n"
        compose = b"services: {}\n"
        manifest_path, compose_path = self.write(
            "library",
            manifest,
            compose=compose,
            compose_file="nested/compose.yaml",
        )
        assert compose_path is not None
        planned = definition(
            manifest, compose=compose, compose_file="nested/compose.yaml"
        )
        targets = [
            self.roots["library"],
            manifest_path.parent,
            compose_path.parent,
            manifest_path,
        ]
        for target in targets:
            original = stat.S_IMODE(target.stat().st_mode)
            with self.subTest(target=target.name):
                target.chmod(original | 0o020)
                self.assert_code(
                    "artifact-custody-violation",
                    lambda: verifier.verify_planned_definition(
                        planned, self.injected
                    ),
                )
                target.chmod(original)

    def test_oversized_file_is_rejected_before_canonicalization(self) -> None:
        payload = b"x" * (verifier.MAX_ARTIFACT_BYTES + 1)
        self.write("library", payload)
        planned = replace(
            definition(b"name: demo\n"),
            definition_sha256="sha256:" + "0" * 64,
        )
        self.assert_code(
            "artifact-size-exceeded",
            lambda: verifier.verify_planned_definition(planned, self.injected),
        )

    def test_tampering_duplicate_keys_and_invalid_utf8_fail_distinctly(self) -> None:
        expected = b"name: expected\n"
        cases = [
            (b"name: different\n", "artifact-digest-mismatch", None),
            (
                b"name: first\nname: second\n",
                "artifact-canonicalization-failed",
                "canonical-document-parse-error",
            ),
            (
                b"name: \xff\n",
                "artifact-canonicalization-failed",
                "canonical-document-parse-error",
            ),
        ]
        for index, (payload, code, cause) in enumerate(cases):
            service_id = f"demo-{index}"
            self.write("library", payload, service_id=service_id)
            planned = definition(expected, service_id=service_id)
            with self.subTest(code=code):
                error = self.assert_code(
                    code,
                    lambda planned=planned: verifier.verify_planned_definition(
                        planned, self.injected
                    ),
                )
                self.assertEqual(error.cause_code, cause)

    def test_in_place_change_during_read_is_rejected(self) -> None:
        manifest = b"name: demo\n"
        manifest_path, _ = self.write("library", manifest)
        planned = definition(manifest)
        real_read = os.read
        changed = False

        def drifting_read(descriptor: int, size: int) -> bytes:
            nonlocal changed
            result = real_read(descriptor, size)
            if result and not changed:
                changed = True
                manifest_path.write_bytes(manifest + b"# changed\n")
            return result

        with mock.patch.object(verifier.os, "read", side_effect=drifting_read):
            self.assert_code(
                "artifact-state-changed",
                lambda: verifier.verify_planned_definition(planned, self.injected),
            )

    def test_verification_does_not_mutate_the_source_tree(self) -> None:
        manifest = b"name: demo\n"
        compose = b"services: {}\n"
        self.write("library", manifest, compose=compose)

        def inventory() -> list[tuple[str, bytes, int]]:
            return sorted(
                (
                    path.relative_to(self.base).as_posix(),
                    path.read_bytes(),
                    stat.S_IMODE(path.stat().st_mode),
                )
                for path in self.base.rglob("*")
                if path.is_file()
            )

        before = inventory()
        verifier.verify_planned_definition(
            definition(manifest, compose=compose, compose_file="compose.yaml"),
            self.injected,
        )
        self.assertEqual(inventory(), before)

    def test_plan_bound_full_library_tree_matches_supporting_payload(self) -> None:
        manifest = b"name: demo\n"
        compose = b"services: {}\n"
        self.write("library", manifest, compose=compose)
        service = self.roots["library"] / "demo"
        (service / "config").mkdir(mode=0o700)
        settings = service / "config" / "settings.yml"
        settings.write_bytes(b"safe: true\n")
        settings.chmod(0o600)
        expected = digest_extension_tree(service)
        planned = replace(
            definition(manifest, compose=compose, compose_file="compose.yaml"),
            source_tree_sha256=expected,
        )

        result = verifier.verify_planned_definition(planned, self.injected)
        self.assertEqual(result.manifest.content, manifest)
        self.assertEqual(result.compose.content, compose)

    def test_changed_supporting_library_file_fails_before_staging(self) -> None:
        manifest = b"name: demo\n"
        compose = b"services: {}\n"
        for kind, path in (
            ("config", "config/settings.yml"),
            ("hook", "hooks/setup.sh"),
            ("build", "build/Dockerfile"),
            ("doc", "README.md"),
        ):
            with self.subTest(kind=kind):
                service_id = f"demo-{kind}"
                self.write("library", manifest, service_id=service_id, compose=compose)
                service = self.roots["library"] / service_id
                supporting = service / path
                supporting.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                supporting.write_bytes(b"approved\n")
                supporting.chmod(0o600)
                expected = digest_extension_tree(service)
                supporting.write_bytes(b"unapproved\n")
                planned = replace(
                    definition(
                        manifest,
                        service_id=service_id,
                        compose=compose,
                        compose_file="compose.yaml",
                    ),
                    source_tree_sha256=expected,
                )
                self.assert_code(
                    "artifact-library-tree-mismatch",
                    lambda: verifier.verify_planned_definition(planned, self.injected),
                )

    def test_group_writable_supporting_library_file_is_refused(self) -> None:
        manifest = b"name: demo\n"
        self.write("library", manifest)
        service = self.roots["library"] / "demo"
        supporting = service / "README.md"
        supporting.write_bytes(b"approved\n")
        supporting.chmod(0o600)
        expected = digest_extension_tree(service)
        supporting.chmod(0o660)
        planned = replace(definition(manifest), source_tree_sha256=expected)
        self.assert_code(
            "artifact-library-tree-invalid",
            lambda: verifier.verify_planned_definition(planned, self.injected),
        )

    def test_library_tree_symlink_and_wrong_source_are_refused(self) -> None:
        manifest = b"name: demo\n"
        self.write("library", manifest)
        service = self.roots["library"] / "demo"
        expected = digest_extension_tree(service)
        (service / "README.md").symlink_to(service / "manifest.yaml")
        planned = replace(definition(manifest), source_tree_sha256=expected)
        self.assert_code(
            "artifact-library-tree-invalid",
            lambda: verifier.verify_planned_definition(planned, self.injected),
        )
        self.assert_code(
            "artifact-plan-invalid",
            lambda: verifier.verify_planned_definition(
                replace(planned, definition_source="builtin"), self.injected
            ),
        )

    def test_library_tree_changed_between_artifact_reads_is_refused(self) -> None:
        manifest = b"name: demo\n"
        self.write("library", manifest)
        expected = digest_extension_tree(self.roots["library"] / "demo")
        planned = replace(definition(manifest), source_tree_sha256=expected)
        with mock.patch.object(
            verifier, "digest_extension_tree", side_effect=[expected, "sha256:" + "0" * 64]
        ) as digest:
            self.assert_code(
                "artifact-library-tree-mismatch",
                lambda: verifier.verify_planned_definition(planned, self.injected),
            )
        self.assertEqual(digest.call_count, 2)

    def test_module_has_no_effect_or_discovery_primitives(self) -> None:
        source_path = BIN_DIR / "extension_artifact_verifier.py"
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
        self.assertTrue(imports.isdisjoint({"subprocess", "socket", "urllib"}))
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
            "unlink",
            "write",
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
