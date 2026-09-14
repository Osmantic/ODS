#!/usr/bin/env python3
"""Contracts for the opt-in Assistant First installation profile."""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import shutil
import subprocess
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
RESOLVER = ROOT / "scripts" / "resolve-compose-stack.sh"
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
        self.assertIn("--assistant-first", installer)
        self.assertIn('ODS_INSTALL_PROFILE="${ODS_INSTALL_PROFILE:-legacy}"', installer)
        self.assertIn("fresh installs during public beta", installer)
        self.assertIn("fresh installs during public beta", features)

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
        }
        rendered = subprocess.run(
            command,
            check=True,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        services = set(json.loads(rendered.stdout)["services"])
        self.assertEqual(services, MINIMUM_SERVICES)
        self.assertTrue(EXTRACTED_OPTIONAL.isdisjoint(services))


if __name__ == "__main__":
    unittest.main()
