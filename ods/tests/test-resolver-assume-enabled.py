"""The Compose resolver can preview a bundled service before it is selected.

Image preparation asks for the stack as it will be once a bundled service is
selected, so it downloads exactly the images `up` will use. The preview never
widens to Library recipes and never changes what is selected.
"""

import os
from pathlib import Path
import subprocess

import pytest

RESOLVER = Path(__file__).resolve().parents[1] / "scripts" / "resolve-compose-stack.sh"
MANIFEST = """schema_version: ods.services.v1
service:
  id: {id}
  compose_file: compose.yaml
  gpu_backends: [all]
"""


@pytest.fixture
def install(tmp_path):
    (tmp_path / "docker-compose.base.yml").write_text(
        "services:\n  dashboard-api:\n    image: example/dashboard-api:1\n", encoding="utf-8")
    bundled = tmp_path / "extensions" / "services" / "demo"
    bundled.mkdir(parents=True)
    (bundled / "manifest.yaml").write_text(MANIFEST.format(id="demo"), encoding="utf-8")
    (bundled / "compose.yaml.disabled").write_text(
        "services:\n  demo:\n    image: example/demo:1\n", encoding="utf-8")
    (bundled / "compose.cpu.yaml").write_text(
        "services:\n  demo:\n    image: example/demo:1-cpu\n", encoding="utf-8")
    library = tmp_path / "data" / "user-extensions" / "recipe"
    library.mkdir(parents=True)
    (library / "manifest.yaml").write_text(MANIFEST.format(id="recipe"), encoding="utf-8")
    (library / "compose.yaml.disabled").write_text(
        "services:\n  recipe:\n    image: example/recipe:1\n", encoding="utf-8")
    return tmp_path


def resolve(root, *extra):
    env = {**os.environ, "ODS_MODE": "local"}
    for selector in ("ODS_RESOLVE_ASSUME_ENABLED", "ODS_SKIP_GPU_OVERLAYS", "ENABLE_OPEN_WEBUI"):
        env.pop(selector, None)
    result = subprocess.run(
        ["bash", str(RESOLVER), "--script-dir", str(root), "--tier", "1", "--gpu-backend", "cpu",
         "--gpu-count", "1", "--ods-mode", "local", *extra],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.split()


def test_a_disabled_bundled_service_is_left_out(install):
    flags = resolve(install)
    assert not any("extensions/services/demo" in flag for flag in flags)


def test_preview_resolves_the_files_its_selection_will_use(install):
    flags = resolve(install, "--assume-enabled", "demo")
    assert "extensions/services/demo/compose.yaml.disabled" in flags
    assert "extensions/services/demo/compose.cpu.yaml" in flags
    # Nothing was selected.
    assert (install / "extensions/services/demo/compose.yaml.disabled").is_file()
    assert not (install / "extensions/services/demo/compose.yaml").exists()


def test_preview_never_includes_library_recipes(install):
    flags = resolve(install, "--assume-enabled", "demo,recipe")
    assert not any("user-extensions/recipe" in flag for flag in flags)
