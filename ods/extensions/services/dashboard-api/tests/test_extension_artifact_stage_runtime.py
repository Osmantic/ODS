"""Dormant production composition for verified artifact staging."""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_artifact_stage_adapter as adapter  # noqa: E402
import extension_artifact_stage_recovery as recovery  # noqa: E402
import extension_artifact_stage_runtime as runtime  # noqa: E402
import extension_artifact_stage_store as staging  # noqa: E402


SUPPORTED = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
)


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True)
    path.chmod(0o700)


@unittest.skipUnless(SUPPORTED, "requires Linux dir_fd semantics")
class ArtifactStageRuntimeTests(unittest.TestCase):
    def test_builds_exact_unregistered_dependencies_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data = base / "data"
            stage = data / "assistant-first" / "artifact-stage"
            builtins = base / "extensions"
            users = data / "user-extensions"
            for path in (stage, builtins, users):
                _private_directory(path)

            composed = runtime.build_artifact_stage_runtime(
                data_dir=data,
                builtin_root=builtins,
                library_root=builtins,
                user_root=users,
            )

            self.assertIs(type(composed), runtime.ArtifactStageRuntime)
            self.assertEqual(composed.root, stage)
            self.assertIs(type(composed.store), staging.ArtifactStageStore)
            self.assertIs(type(composed.dispatcher), adapter.ArtifactStageAdapter)
            self.assertIs(
                type(composed.started_observer),
                recovery.ArtifactStageRecoveryObserver,
            )
            self.assertEqual(list(stage.iterdir()), [])

    def test_root_validation_fails_closed_without_creating_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data = base / "data"
            builtins = base / "extensions"
            users = data / "user-extensions"
            for path in (builtins, users):
                _private_directory(path)

            with self.assertRaises(staging.ArtifactStageError) as missing:
                runtime.build_artifact_stage_runtime(
                    data_dir=data,
                    builtin_root=builtins,
                    library_root=builtins,
                    user_root=users,
                )
            self.assertEqual(missing.exception.code, "artifact-stage-root-missing")
            self.assertFalse((data / "assistant-first").exists())

            stage = data / "assistant-first" / "artifact-stage"
            _private_directory(stage)
            stage.chmod(0o755)
            with self.assertRaises(staging.ArtifactStageError) as unsafe:
                runtime.build_artifact_stage_runtime(
                    data_dir=data,
                    builtin_root=builtins,
                    library_root=builtins,
                    user_root=users,
                )
            self.assertEqual(
                unsafe.exception.code, "artifact-stage-custody-violation"
            )

    def test_symlinked_stage_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data = base / "data"
            private = data / "assistant-first"
            target = base / "target"
            builtins = base / "extensions"
            users = data / "user-extensions"
            for path in (private, target, builtins, users):
                _private_directory(path)
            (private / "artifact-stage").symlink_to(
                target, target_is_directory=True
            )

            with self.assertRaises(staging.ArtifactStageError) as rejected:
                runtime.build_artifact_stage_runtime(
                    data_dir=data,
                    builtin_root=builtins,
                    library_root=builtins,
                    user_root=users,
                )
            self.assertIn(
                rejected.exception.code,
                {"artifact-stage-root-invalid", "artifact-stage-io-error"},
            )
            self.assertEqual(list(target.iterdir()), [])

    def test_host_factory_is_fixed_cached_and_still_dormant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data = base / "data"
            stage = data / "assistant-first" / "artifact-stage"
            builtins = base / "extensions"
            users = data / "user-extensions"
            other_users = data / "other-user-extensions"
            for path in (stage, builtins, users, other_users):
                _private_directory(path)

            agent_path = BIN_DIR / "ods-host-agent.py"
            spec = importlib.util.spec_from_file_location(
                "_extension_artifact_stage_runtime_host_agent",
                agent_path,
            )
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            agent = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = agent
            try:
                spec.loader.exec_module(agent)
                agent.DATA_DIR = data
                agent.EXTENSIONS_DIR = builtins
                agent.USER_EXTENSIONS_DIR = users
                agent._artifact_stage_runtime = None
                agent._artifact_stage_runtime_binding = None

                first = agent._get_extension_artifact_stage_runtime()
                replay = agent._get_extension_artifact_stage_runtime()
                self.assertIs(first, replay)
                self.assertEqual(first.root, stage)
                self.assertIsNone(agent._extension_lifecycle_work_dispatcher)

                agent.USER_EXTENSIONS_DIR = other_users
                rebound = agent._get_extension_artifact_stage_runtime()
                self.assertIsNot(rebound, first)
                self.assertEqual(rebound.root, stage)
                self.assertIsNone(agent._extension_lifecycle_work_dispatcher)
            finally:
                sys.modules.pop(spec.name, None)

    def test_host_factory_has_no_production_caller(self) -> None:
        repo = Path(__file__).resolve().parents[5]
        callers: list[str] = []
        for path in (repo / "ods").rglob("*.py"):
            if "tests" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                if (
                    isinstance(function, ast.Name)
                    and function.id == "_get_extension_artifact_stage_runtime"
                ) or (
                    isinstance(function, ast.Attribute)
                    and function.attr == "_get_extension_artifact_stage_runtime"
                ):
                    callers.append(f"{path.relative_to(repo)}:{node.lineno}")

        self.assertEqual(callers, [])
        host_source = (BIN_DIR / "ods-host-agent.py").read_text(encoding="utf-8")
        self.assertIn("_extension_lifecycle_work_dispatcher = None", host_source)


if __name__ == "__main__":
    unittest.main()
