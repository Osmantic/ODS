"""A bundled service's compose.arm64.yaml joins the stack on arm64 hosts.

TEI publishes no arm64 image under its release tags, so arm64 hosts run the
arm64 pin in extensions/services/embeddings/compose.arm64.yaml; without it TEI
crash-looped on "exec format error" (Workstation06, 2026-10-27). Docker
Desktop on macOS keeps the base image under Rosetta 2.
"""

import os
import platform
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / "scripts" / "resolve-compose-stack.sh"
BASE = "extensions/services/demo/compose.yaml"
ARM64 = "extensions/services/demo/compose.arm64.yaml"
MANIFEST = """schema_version: ods.services.v1
service:
  id: demo
  compose_file: compose.yaml
  gpu_backends: [all]
"""


@pytest.fixture
def install(tmp_path):
    (tmp_path / "docker-compose.base.yml").write_text(
        "services:\n  dashboard-api:\n    image: example/dashboard-api:1\n", encoding="utf-8")
    bundled = tmp_path / "extensions" / "services" / "demo"
    bundled.mkdir(parents=True)
    (bundled / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    (bundled / "compose.yaml").write_text(
        "services:\n  demo:\n    image: example/demo:1\n", encoding="utf-8")
    (bundled / "compose.arm64.yaml").write_text(
        "services:\n  demo:\n    image: example/demo:1-arm64\n    platform: linux/arm64\n",
        encoding="utf-8")
    return tmp_path


def resolve(root, host_arch):
    env = {**os.environ, "ODS_MODE": "local", "HOST_ARCH": host_arch}
    for selector in ("ODS_RESOLVE_ASSUME_ENABLED", "ODS_SKIP_GPU_OVERLAYS", "ENABLE_OPEN_WEBUI"):
        env.pop(selector, None)
    result = subprocess.run(
        ["bash", str(RESOLVER), "--script-dir", str(root), "--tier", "1", "--gpu-backend", "cpu",
         "--gpu-count", "1", "--ods-mode", "local"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return [flag.replace("\\", "/") for flag in result.stdout.split()]


@pytest.mark.skipif(platform.system() == "Darwin",
                    reason="Docker Desktop on macOS runs the base image under Rosetta 2")
@pytest.mark.parametrize("host_arch", ["arm64", "aarch64"])
def test_an_arm64_host_layers_the_arm64_pin_over_the_base(install, host_arch):
    flags = resolve(install, host_arch)
    assert BASE in flags and ARM64 in flags
    assert flags.index(ARM64) > flags.index(BASE)


@pytest.mark.parametrize("host_arch", ["amd64", "x86_64"])
def test_an_amd64_host_keeps_the_base_image(install, host_arch):
    flags = resolve(install, host_arch)
    assert BASE in flags
    assert ARM64 not in flags


def test_a_disabled_service_brings_no_arm64_pin(install):
    (install / BASE).rename(install / f"{BASE}.disabled")
    assert not any("extensions/services/demo" in flag for flag in resolve(install, "arm64"))
