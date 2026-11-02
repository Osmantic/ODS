"""A bundled service whose image this host cannot run stays out of the stack.

The Dashboard marks it incompatible and hides it (manifest host_requirements,
#8257). Leaving it out of Compose as well means a service selected before that,
such as Qdrant on 64 KB pages or Fooocus on arm64 (Workstation06, 2026-10-27),
cannot keep crash-looping with no card left to disable it.
"""

import os
import platform
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / "scripts" / "resolve-compose-stack.sh"
BASE = "extensions/services/demo/compose.yaml"


def install(tmp_path, requirements):
    (tmp_path / "docker-compose.base.yml").write_text(
        "services:\n  dashboard-api:\n    image: example/dashboard-api:1\n", encoding="utf-8")
    bundled = tmp_path / "extensions" / "services" / "demo"
    bundled.mkdir(parents=True)
    (bundled / "manifest.yaml").write_text(
        "schema_version: ods.services.v1\nservice:\n  id: demo\n  compose_file: compose.yaml\n"
        f"  gpu_backends: [all]\n  host_requirements:\n    {requirements}\n", encoding="utf-8")
    (bundled / "compose.yaml").write_text(
        "services:\n  demo:\n    image: example/demo:1\n", encoding="utf-8")
    return tmp_path


def resolve(root, host_arch, page_size):
    env = {**os.environ, "ODS_MODE": "local", "HOST_ARCH": host_arch, "HOST_PAGE_SIZE": page_size}
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
                    reason="Docker Desktop on macOS runs a 4 KB-page VM with Rosetta 2")
@pytest.mark.parametrize("requirements,host_arch,page_size,included", [
    ("max_page_size: 4096", "arm64", "65536", False),
    ("max_page_size: 4096", "aarch64", "16384", False),
    ("max_page_size: 4096", "arm64", "4096", True),
    ("architectures: [amd64]", "arm64", "4096", False),
    ("architectures: [amd64]", "aarch64", "4096", False),
    ("architectures: [amd64]", "amd64", "4096", True),
    ("architectures: [amd64]", "x86_64", "4096", True),
    # A malformed override is ignored rather than crashing every ods command.
    ("max_page_size: 4096", "amd64", "lots", True),
])
def test_a_service_runs_only_where_its_image_can(tmp_path, requirements, host_arch, page_size, included):
    flags = resolve(install(tmp_path, requirements), host_arch, page_size)
    assert (BASE in flags) is included
