"""Shipped extensions that build their own image enable only with the exact
folder ODS shipped.

Token Spy, APE, Privacy Shield and Brave Search build from their own
extension folder. dashboard-api accepts such a build only when the folder,
compose files aside, matches the digest pinned in builtin_source_recipes.py:
no file added (a planted .pyc), changed or removed, and no links.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

import builtin_source_recipes as recipes
from builtin_source_recipes import verify_builtin_source_build
from routers import extensions

ODS = Path(__file__).resolve().parents[4]
SHIPPED = ODS / "extensions/services"
PIN_SCRIPT = ODS / "scripts/pin-builtin-build-contexts.py"
CONTEXT_SERVICES = sorted(recipes._CONTEXT_BUILDS)


def _pin_script():
    spec = importlib.util.spec_from_file_location("pin_builtin_build_contexts", PIN_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pins_match_the_shipped_folders():
    """Fails when a pinned folder changes; run the script it names to re-pin."""
    current = _pin_script().current_pins()
    pinned = {service: entry[3] for service, entry in recipes._CONTEXT_BUILDS.items()}
    assert current == pinned, "run: python3 scripts/pin-builtin-build-contexts.py --write"


@pytest.fixture()
def shipped(tmp_path, monkeypatch):
    """Copies of the shipped folders under a patched EXTENSIONS_DIR."""
    root = tmp_path / "EXTENSIONS_DIR"
    root.mkdir()
    for service in CONTEXT_SERVICES:
        folder = recipes._CONTEXT_BUILDS[service][0]
        destination = root / folder
        shutil.copytree(SHIPPED / folder, destination, symlinks=False,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if not (destination / "compose.yaml").exists():
            shutil.copyfile(destination / "compose.yaml.disabled", destination / "compose.yaml")
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", root)
    return root


def _compose(root: Path, service: str) -> Path:
    return root / recipes._CONTEXT_BUILDS[service][0] / "compose.yaml"


def _verify(root: Path, service: str) -> bool:
    compose = _compose(root, service)
    definition = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"][service]
    return verify_builtin_source_build(compose, service, definition, root)


@pytest.mark.parametrize("service", CONTEXT_SERVICES)
def test_the_shipped_folder_is_accepted(shipped, service):
    assert _verify(shipped, service) is True


@pytest.mark.parametrize("service", CONTEXT_SERVICES)
def test_the_router_enables_the_shipped_build(shipped, service):
    extensions._scan_compose_content(
        _compose(shipped, service), skip_name_collision=True, skip_gpu_passthrough_check=True,
        skip_root_user_check=True, builtin=True)


@pytest.mark.parametrize("service", CONTEXT_SERVICES)
def test_a_changed_file_is_refused(shipped, service):
    dockerfile = shipped / recipes._CONTEXT_BUILDS[service][0] / "Dockerfile"
    dockerfile.write_text(dockerfile.read_text(encoding="utf-8") + "RUN true\n", encoding="utf-8")
    assert _verify(shipped, service) is False


@pytest.mark.parametrize("service", CONTEXT_SERVICES)
def test_an_added_file_is_refused(shipped, service):
    cache = shipped / recipes._CONTEXT_BUILDS[service][0] / "__pycache__"
    cache.mkdir()
    (cache / "main.cpython-312.pyc").write_bytes(b"\x00planted")
    assert _verify(shipped, service) is False


def test_a_removed_file_is_refused(shipped):
    (shipped / "token-spy" / "filters.py").unlink()
    assert _verify(shipped, "token-spy") is False


def test_a_link_in_the_folder_is_refused(shipped, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    link = shipped / "token-spy" / "providers" / "linked.py"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlinks need extra privileges on this platform")
    assert _verify(shipped, "token-spy") is False


@pytest.mark.parametrize("change", [
    {"build": {"context": "./extensions/services/token-spy", "dockerfile": "Dockerfile.other"}},
    {"build": {"context": "./extensions/services/ape", "dockerfile": "Dockerfile"}},
    {"image": "example/token-spy:latest"},
])
def test_a_different_build_definition_is_refused(shipped, change):
    compose = _compose(shipped, "token-spy")
    definition = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]["token-spy"]
    definition.update(change)
    assert verify_builtin_source_build(compose, "token-spy", definition, shipped) is False


def test_a_changed_folder_cannot_be_enabled(shipped):
    main = shipped / "token-spy" / "main.py"
    main.write_text(main.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    with pytest.raises(HTTPException) as rejected:
        extensions._scan_compose_content(
            _compose(shipped, "token-spy"), skip_name_collision=True, skip_gpu_passthrough_check=True,
            skip_root_user_check=True, builtin=True)
    assert rejected.value.status_code == 400
    assert "verified source recipe" in str(rejected.value.detail)


def test_an_untrusted_copy_is_never_accepted(shipped):
    """The pin vouches only for the shipped folder, not for user extensions."""
    with pytest.raises(HTTPException):
        extensions._scan_compose_content(
            _compose(shipped, "token-spy"), skip_name_collision=True, skip_gpu_passthrough_check=True,
            skip_root_user_check=True, builtin=False)
