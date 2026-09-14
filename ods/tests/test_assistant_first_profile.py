#!/usr/bin/env python3
"""Contracts for the opt-in Assistant First installation profile."""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
RESOLVER = ROOT / "scripts" / "resolve-compose-stack.sh"
CUSTODY_HELPER = ROOT / "installers" / "lib" / "assistant-first-state.sh"
MINIMUM_SERVICES = {
    "dashboard",
    "dashboard-api",
    "llama-server",
    "model-router",
    "pixel-edge",
}
EXTRACTED_OPTIONAL = {
    "open-webui",
    "remote-provider-egress",
    "remote-provider-ssh-tunnel",
}


def resolve(*args: str, env: dict[str, str] | None = None) -> list[str]:
    command = [
        "bash",
        str(RESOLVER),
        "--script-dir",
        str(ROOT),
        "--tier",
        "1",
        "--gpu-backend",
        "nvidia",
        "--ods-mode",
        "local",
        *args,
    ]
    result = subprocess.run(
        command,
        check=True,
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
    )
    words = shlex.split(result.stdout)
    return [words[index + 1] for index, word in enumerate(words) if word == "-f"]


class AssistantFirstProfileTests(unittest.TestCase):
    def test_optional_services_are_fragments_not_base_owners(self) -> None:
        base = yaml.safe_load((ROOT / "docker-compose.base.yml").read_text(encoding="utf-8"))
        services = set(base["services"])
        self.assertTrue(EXTRACTED_OPTIONAL.isdisjoint(services))
        self.assertEqual(
            services,
            {"dashboard", "dashboard-api", "llama-server", "model-router"},
        )
        for service_id in EXTRACTED_OPTIONAL:
            fragment = yaml.safe_load(
                (ROOT / "extensions" / "services" / service_id / "compose.yaml").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn(service_id, fragment["services"])

    def test_assistant_first_resolves_only_candidate_minimum(self) -> None:
        files = resolve("--install-profile", "assistant-first")
        self.assertEqual(
            files,
            [
                "docker-compose.base.yml",
                "docker-compose.nvidia.yml",
                "extensions/services/pixel-edge/compose.assistant-first.yaml",
            ],
        )
        self.assertTrue(all("open-webui" not in item for item in files))
        self.assertTrue(all("remote-provider" not in item for item in files))

        for backend, overlay in (
            ("amd", "docker-compose.amd.yml"),
            ("cpu", "docker-compose.cpu.yml"),
            ("intel", "docker-compose.arc.yml"),
        ):
            backend_files = resolve(
                "--gpu-backend", backend, "--install-profile", "assistant-first"
            )
            self.assertEqual(backend_files[0:2], ["docker-compose.base.yml", overlay])
            self.assertEqual(
                backend_files[-1],
                "extensions/services/pixel-edge/compose.assistant-first.yaml",
            )

    def test_external_modes_replace_managed_inference_without_optional_apps(self) -> None:
        cloud = resolve(
            "--tier", "CLOUD", "--ods-mode", "cloud", "--install-profile", "assistant-first"
        )
        self.assertEqual(
            cloud,
            [
                "docker-compose.base.yml",
                "docker-compose.cloud.yml",
                "extensions/services/litellm/compose.yaml",
                "extensions/services/pixel-edge/compose.assistant-first.yaml",
            ],
        )
        external = resolve(
            "--install-profile",
            "assistant-first",
            env={"EXTERNAL_LLM_URL": "http://host.docker.internal:9999"},
        )
        self.assertEqual(external[-1], "docker-compose.external-llm.yml")
        self.assertTrue(all("open-webui" not in item for item in external))

    def test_legacy_resolver_retains_extracted_services(self) -> None:
        files = resolve("--install-profile", "legacy")
        for expected in (
            "extensions/services/open-webui/compose.yaml",
            "extensions/services/open-webui/compose.nvidia.yaml",
            "extensions/services/remote-provider-egress/compose.yaml",
            "extensions/services/remote-provider-ssh-tunnel/compose.yaml",
        ):
            self.assertIn(expected, files)

    def test_manifest_capabilities_replace_fixed_assistant_dependencies(self) -> None:
        assistant = yaml.safe_load(
            (ROOT / "extensions/services/pixel-agent/manifest.yaml").read_text(encoding="utf-8")
        )
        service = assistant["service"]
        self.assertEqual(service["depends_on"], [])
        self.assertEqual(service["capabilities"]["requires"], ["inference-route@1"])
        self.assertEqual(service["capabilities"]["optional"], ["web-search@1"])
        llama = yaml.safe_load(
            (ROOT / "extensions/services/llama-server/manifest.yaml").read_text(encoding="utf-8")
        )
        self.assertIn("inference-route@1", llama["service"]["capabilities"]["provides"])
        litellm = yaml.safe_load(
            (ROOT / "extensions/services/litellm/manifest.yaml").read_text(encoding="utf-8")
        )
        self.assertIn("inference-route@1", litellm["service"]["capabilities"]["provides"])

    def test_image_phase_owns_optional_images_through_compose(self) -> None:
        phase = (ROOT / "installers/phases/08-images.sh").read_text(encoding="utf-8")
        self.assertNotIn("ghcr.io/open-webui/open-webui", phase)
        self.assertNotIn("itzcrazykns1337/perplexica", phase)
        self.assertIn("ods_compose_external_images", phase)

        directories = (ROOT / "installers/phases/06-directories.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('if [[ "${ODS_INSTALL_PROFILE:-legacy}" != "assistant-first" ]]', directories)
        self.assertIn('if [[ "${ENABLE_SEARXNG:-false}" == "true" ]]', directories)

        health = (ROOT / "installers/phases/12-health.sh").read_text(encoding="utf-8")
        self.assertIn('"Dashboard API"', health)
        self.assertIn('"Assistant"', health)
        self.assertIn("Assistant gateway, private ingress, and edge", health)

        devtools = (ROOT / "installers/phases/07-devtools.sh").read_text(encoding="utf-8")
        self.assertIn("Assistant First skips optional developer tools", devtools)
        self.assertIn(
            'if [[ "${ODS_INSTALL_PROFILE:-legacy}" != "assistant-first" ]]',
            devtools,
        )

        self.assertIn("assistant-plan-placeholder", phase)

        detection = (ROOT / "installers/phases/02-detection.sh").read_text(encoding="utf-8")
        self.assertIn("The assistant selected the strongest installable", detection)

    def test_profile_is_opt_in_and_blocks_legacy_conversion(self) -> None:
        installer = (ROOT / "install-core.sh").read_text(encoding="utf-8")
        features = (ROOT / "installers/phases/03-features.sh").read_text(encoding="utf-8")
        directories = (ROOT / "installers/phases/06-directories.sh").read_text(
            encoding="utf-8"
        )
        custody = CUSTODY_HELPER.read_text(encoding="utf-8")
        base = yaml.safe_load(
            (ROOT / "docker-compose.base.yml").read_text(encoding="utf-8")
        )
        assistant_fragment = yaml.safe_load(
            (
                ROOT
                / "extensions/services/pixel-edge/compose.assistant-first.yaml"
            ).read_text(encoding="utf-8")
        )
        self.assertIn("--assistant-first", installer)
        self.assertIn('ODS_INSTALL_PROFILE="${ODS_INSTALL_PROFILE:-legacy}"', installer)
        self.assertIn("fresh installs during public beta", installer)
        self.assertIn("fresh installs during public beta", features)
        self.assertIn(
            "ODS_ASSISTANT_TRANSACTIONS_ENABLED_VALUE=true", directories
        )
        self.assertIn(
            "ODS_ASSISTANT_TRANSACTIONS_ENABLED=${ODS_ASSISTANT_TRANSACTIONS_ENABLED_VALUE}",
            directories,
        )
        self.assertIn(
            "ODS_ASSISTANT_TRANSACTIONS_ENABLED=${ODS_ASSISTANT_TRANSACTIONS_ENABLED:-false}",
            base["services"]["dashboard-api"]["environment"],
        )
        self.assertEqual(
            assistant_fragment["services"]["dashboard-api"]["user"],
            "${ODS_UID:-1000}:${ODS_GID:-1000}",
        )
        self.assertIn("ods_assistant_first_prepare_state_directories", directories)
        self.assertIn("data_root/assistant-first", custody)
        self.assertIn("data_root/assistant-first/application-state", custody)
        self.assertIn("data_root/.extension-operation-locks", custody)
        self.assertIn("for child_name in config models persona", custody)
        self.assertIn("umask 077 && mkdir --", custody)
        self.assertIn("Assistant First requires ODS_UID to match", directories)
        self.assertIn("not yet qualified for rootless Docker", directories)
        self.assertIn("not owned by the installing host UID", custody)
        create_step = directories.index('_phase06_step "create-directories"')
        uid_guard = directories.index(
            "Assistant First requires ODS_UID to match", create_step
        )
        custody_call = directories.index(
            "ods_assistant_first_prepare_state_directories", uid_guard
        )
        generic_children = directories.index(
            'mkdir -p "$INSTALL_DIR"/data/{config,models,persona}', custody_call
        )
        self.assertLess(uid_guard, custody_call)
        self.assertLess(custody_call, generic_children)

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI is unavailable")
    def test_candidate_minimum_compose_renders_without_optional_services(self) -> None:
        try:
            subprocess.run(
                ["docker", "compose", "version"],
                cwd=ROOT,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("Docker Compose is unavailable")

        files = resolve("--install-profile", "assistant-first")
        command = ["docker", "compose"]
        for item in files:
            command.extend(("-f", item))
        command.extend(("config", "--format", "json"))
        env = {
            **os.environ,
            "PIXEL_OPENWEBUI_KEY": "a" * 64,
            "DASHBOARD_API_KEY": "b" * 64,
            "PIXEL_INGRESS_GID": "1234",
            "PIXEL_INGRESS_RUNTIME_DIR": "/tmp/ods-assistant-first-ingress",
            "PIXEL_PREVIEW_RUNTIME_DIR": "/tmp/ods-assistant-first-preview",
            "ODS_UID": "2345",
            "ODS_GID": "3456",
        }
        rendered = subprocess.run(
            command,
            check=True,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        rendered_compose = json.loads(rendered.stdout)
        services = set(rendered_compose["services"])
        self.assertEqual(services, MINIMUM_SERVICES)
        self.assertTrue(EXTRACTED_OPTIONAL.isdisjoint(services))
        self.assertEqual(
            rendered_compose["services"]["dashboard-api"]["user"],
            "2345:3456",
        )


@unittest.skipUnless(
    sys.platform.startswith("linux") and shutil.which("bash"),
    "Assistant First directory custody is qualified on Linux",
)
class AssistantFirstStateCustodyTests(unittest.TestCase):
    _SCRIPT = r"""
set -uo pipefail
error() { printf '%s\n' "$*" >&2; }
source "$1"
umask "${ODS_TEST_UMASK:-022}"
ods_assistant_first_prepare_state_directories "$2" "$3"
"""

    def _run(
        self,
        data_root: pathlib.Path,
        *,
        expected_uid: int | str | None = None,
        ambient_umask: str = "022",
    ) -> subprocess.CompletedProcess[str]:
        uid = os.getuid() if expected_uid is None else expected_uid
        return subprocess.run(
            [
                "bash",
                "-c",
                self._SCRIPT,
                "assistant-first-state-test",
                str(CUSTODY_HELPER),
                str(data_root),
                str(uid),
            ],
            cwd=ROOT,
            env={**os.environ, "ODS_TEST_UMASK": ambient_umask},
            text=True,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def _mode(path: pathlib.Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_fresh_state_root_accepts_real_transaction_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            install = pathlib.Path(temp) / "install"
            install.mkdir()
            data = install / "data"

            result = self._run(data)
            self.assertEqual(result.returncode, 0, result.stderr)
            private = data / "assistant-first"
            stage = private / "artifact-stage"
            reservations = private / "resource-reservations"
            applications = private / "application-state"
            locks = data / ".extension-operation-locks"
            self.assertEqual(self._mode(data), 0o755)
            self.assertEqual(self._mode(private), 0o700)
            self.assertEqual(self._mode(stage), 0o700)
            self.assertEqual(self._mode(reservations), 0o700)
            self.assertEqual(self._mode(applications), 0o700)
            self.assertEqual(self._mode(locks), 0o700)
            for child_name in ("config", "models", "persona"):
                self.assertEqual(self._mode(data / child_name), 0o755)

            api_root = ROOT / "extensions" / "services" / "dashboard-api"
            transaction_root = private / "transaction-store"
            initialized = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib,sys; "
                        "from extension_transactions import TransactionStore; "
                        "TransactionStore(pathlib.Path(sys.argv[1]))"
                    ),
                    str(transaction_root),
                ],
                cwd=api_root,
                env={**os.environ, "PYTHONPATH": str(api_root)},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            self.assertTrue(transaction_root.is_dir())
            self.assertEqual(self._mode(transaction_root), 0o700)

    def test_data_symlink_is_rejected_before_child_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            install = root / "install"
            target = root / "redirect-target"
            install.mkdir()
            target.mkdir()
            (install / "data").symlink_to(target, target_is_directory=True)

            result = self._run(install / "data")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not be a symlink", result.stderr)
            self.assertEqual(list(target.iterdir()), [])

    def test_dangling_data_symlink_is_rejected_before_child_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            install = root / "install"
            install.mkdir()
            data = install / "data"
            data.symlink_to(root / "missing-target", target_is_directory=True)

            result = self._run(data)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not be a symlink", result.stderr)
            self.assertTrue(data.is_symlink())
            self.assertFalse(root.joinpath("missing-target").exists())

    def test_private_symlink_is_rejected_without_creating_lock_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            data = root / "data"
            target = root / "redirect-target"
            data.mkdir(mode=0o755)
            target.mkdir()
            (data / "assistant-first").symlink_to(target, target_is_directory=True)

            result = self._run(data)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not be a symlink", result.stderr)
            self.assertEqual(list(target.iterdir()), [])
            self.assertFalse((data / ".extension-operation-locks").exists())

    def test_dangling_private_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = pathlib.Path(temp) / "data"
            data.mkdir(mode=0o755)
            private = data / "assistant-first"
            private.symlink_to(data / "missing-target", target_is_directory=True)

            result = self._run(data)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not be a symlink", result.stderr)
            self.assertTrue(private.is_symlink())
            self.assertFalse((data / ".extension-operation-locks").exists())

    def test_artifact_stage_symlink_is_rejected_before_sibling_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            data = root / "data"
            private = data / "assistant-first"
            target = root / "redirect-target"
            private.mkdir(mode=0o700, parents=True)
            target.mkdir()
            (private / "artifact-stage").symlink_to(
                target, target_is_directory=True
            )

            result = self._run(data)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not be a symlink", result.stderr)
            self.assertEqual(list(target.iterdir()), [])
            self.assertFalse((data / ".extension-operation-locks").exists())

    def test_artifact_stage_wrong_type_is_rejected_before_sibling_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = pathlib.Path(temp) / "data"
            private = data / "assistant-first"
            private.mkdir(mode=0o700, parents=True)
            (private / "artifact-stage").write_text(
                "not-a-directory", encoding="utf-8"
            )

            result = self._run(data)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("path is not a directory", result.stderr)
            self.assertFalse((data / ".extension-operation-locks").exists())

    def test_resource_reservation_symlink_is_rejected_before_sibling_writes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            data = root / "data"
            private = data / "assistant-first"
            target = root / "redirect-target"
            private.mkdir(mode=0o700, parents=True)
            target.mkdir()
            (private / "resource-reservations").symlink_to(
                target, target_is_directory=True
            )

            result = self._run(data)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not be a symlink", result.stderr)
            self.assertEqual(list(target.iterdir()), [])
            self.assertFalse((private / "artifact-stage").exists())
            self.assertFalse((data / ".extension-operation-locks").exists())

    def test_resource_reservation_wrong_type_is_rejected_before_sibling_writes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = pathlib.Path(temp) / "data"
            private = data / "assistant-first"
            private.mkdir(mode=0o700, parents=True)
            (private / "resource-reservations").write_text(
                "not-a-directory", encoding="utf-8"
            )

            result = self._run(data)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("path is not a directory", result.stderr)
            self.assertFalse((private / "artifact-stage").exists())
            self.assertFalse((data / ".extension-operation-locks").exists())

    def test_application_state_symlink_is_rejected_before_any_sibling_writes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            data = root / "data"
            private = data / "assistant-first"
            target = root / "redirect-target"
            private.mkdir(mode=0o700, parents=True)
            target.mkdir()
            (private / "application-state").symlink_to(
                target, target_is_directory=True
            )

            result = self._run(data)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not be a symlink", result.stderr)
            self.assertEqual(list(target.iterdir()), [])
            self.assertFalse((private / "artifact-stage").exists())
            self.assertFalse((private / "resource-reservations").exists())
            self.assertFalse((data / ".extension-operation-locks").exists())

    def test_wrong_types_and_unexpected_owner_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            data_file = root / "data-file"
            data_file.write_text("not-a-directory", encoding="utf-8")
            wrong_type = self._run(data_file)
            self.assertNotEqual(wrong_type.returncode, 0)
            self.assertIn("not a directory", wrong_type.stderr)

            data = root / "data"
            data.mkdir(mode=0o755)
            wrong_owner = self._run(data, expected_uid=os.getuid() + 1)
            self.assertNotEqual(wrong_owner.returncode, 0)
            self.assertIn("not owned by the installing host UID", wrong_owner.stderr)
            self.assertEqual(list(data.iterdir()), [])

            private_file = data / "assistant-first"
            private_file.write_text("not-a-directory", encoding="utf-8")
            private_type = self._run(data)
            self.assertNotEqual(private_type.returncode, 0)
            self.assertIn("path is not a directory", private_type.stderr)
            self.assertFalse((data / ".extension-operation-locks").exists())

    def test_non_numeric_expected_uid_fails_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = pathlib.Path(temp) / "data"

            result = self._run(data, expected_uid="not-a-uid")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires a numeric host UID", result.stderr)
            self.assertFalse(data.exists())

    def test_existing_modes_are_repaired_idempotently_for_same_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = pathlib.Path(temp) / "data"
            private = data / "assistant-first"
            stage = private / "artifact-stage"
            reservations = private / "resource-reservations"
            applications = private / "application-state"
            locks = data / ".extension-operation-locks"
            data.mkdir(mode=0o777)
            private.mkdir(mode=0o755)
            stage.mkdir(mode=0o755)
            reservations.mkdir(mode=0o755)
            applications.mkdir(mode=0o755)
            locks.mkdir(mode=0o755)
            marker = private / "preserved"
            marker.write_text("state", encoding="utf-8")
            data.chmod(0o777)
            private.chmod(0o755)
            stage.chmod(0o755)
            reservations.chmod(0o755)
            applications.chmod(0o755)
            locks.chmod(0o755)

            first = self._run(data)
            second = self._run(data)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(self._mode(data) & 0o022, 0)
            self.assertEqual(self._mode(private), 0o700)
            self.assertEqual(self._mode(stage), 0o700)
            self.assertEqual(self._mode(reservations), 0o700)
            self.assertEqual(self._mode(applications), 0o700)
            self.assertEqual(self._mode(locks), 0o700)
            self.assertEqual(marker.read_text(encoding="utf-8"), "state")

    def test_preexisting_shared_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            data = root / "data"
            target = root / "redirect-target"
            data.mkdir(mode=0o777)
            target.mkdir()
            (data / "config").symlink_to(target, target_is_directory=True)

            result = self._run(data)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not be a symlink", result.stderr)
            self.assertEqual(list(target.iterdir()), [])
            self.assertFalse((data / "assistant-first").exists())
            self.assertFalse((data / ".extension-operation-locks").exists())

    def test_non_traversable_data_and_hostile_umask_normalize_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = pathlib.Path(temp) / "data"
            data.mkdir(mode=0o644)
            data.chmod(0o644)

            repaired = self._run(data, ambient_umask="000")
            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            self.assertEqual(self._mode(data), 0o755)
            self.assertEqual(self._mode(data / "assistant-first"), 0o700)
            self.assertEqual(
                self._mode(data / "assistant-first" / "artifact-stage"),
                0o700,
            )
            self.assertEqual(
                self._mode(data / "assistant-first" / "resource-reservations"),
                0o700,
            )
            self.assertEqual(
                self._mode(data / "assistant-first" / "application-state"),
                0o700,
            )
            self.assertEqual(self._mode(data / ".extension-operation-locks"), 0o700)
            for child_name in ("config", "models", "persona"):
                self.assertEqual(self._mode(data / child_name), 0o755)

    def test_restrictive_safe_data_mode_is_not_widened(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = pathlib.Path(temp) / "data"
            data.mkdir(mode=0o700)
            data.chmod(0o700)

            result = self._run(data)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self._mode(data), 0o700)


if __name__ == "__main__":
    unittest.main()
