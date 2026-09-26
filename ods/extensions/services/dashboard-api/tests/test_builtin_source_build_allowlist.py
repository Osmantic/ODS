# extensions/services/dashboard-api/tests/test_builtin_source_build_allowlist.py
"""Regression draft for PR6793: exact-byte allowlist for shipped langfuse builds.

The helper verify_builtin_source_build must approve ONLY the exact shipped
langfuse-minio/mc Dockerfiles, exact build dict and image tags, inside the
real EXTENSIONS_DIR/langfuse package, with original (pre-resolve) symlink
identity checked first. Shared policy checks stay in force afterwards.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from builtin_source_recipes import verify_builtin_source_build
from routers import extensions

ODS = Path(__file__).resolve().parents[4]
SHIPPED = ODS / "extensions/services/langfuse"
SERVICES = ("langfuse-minio", "langfuse-minio-init")
COMPOSE_NAMES = ("compose.yaml.disabled", "compose.yaml")


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    """Copy the real shipped langfuse tree under a patched EXTENSIONS_DIR."""
    ext = tmp_path / "EXTENSIONS_DIR"
    dest = ext / "langfuse"
    dest.parent.mkdir(parents=True)
    shutil.copytree(SHIPPED, dest, symlinks=False)
    shutil.copyfile(dest / "compose.yaml.disabled", dest / "compose.yaml")
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", ext)
    return ext


def _svc_defs(compose: Path) -> dict:
    return yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]


def _compose(tree: Path, name: str) -> Path:
    p = tree / "langfuse" / name
    assert p.is_file(), name
    return p


@pytest.mark.parametrize("svc", SERVICES)
@pytest.mark.parametrize("name", COMPOSE_NAMES)
def test_accepts_exact_shipped_build(tree, svc, name):
    compose = _compose(tree, name)
    assert verify_builtin_source_build(
        compose, svc, _svc_defs(compose)[svc], tree) is True


@pytest.mark.parametrize("name", COMPOSE_NAMES)
def test_router_scan_passes_shipped_recipe(tree, name):
    """Parity with BUILTIN_ENABLE_SCAN_PASSES: the copied real tree installs."""
    compose = _compose(tree, name)
    extensions._scan_compose_content(
        compose, skip_name_collision=True, skip_gpu_passthrough_check=True,
        skip_root_user_check=True, builtin=True)


def test_tampered_dockerfile_rejected(tree):
    compose = _compose(tree, "compose.yaml.disabled")
    df = tree / "langfuse" / "Dockerfile.minio"
    original = df.read_bytes()
    df.write_bytes(original + b"\n# x\n")
    assert verify_builtin_source_build(
        compose, "langfuse-minio",
        _svc_defs(compose)["langfuse-minio"], tree) is False
    df.write_bytes(original)
    assert verify_builtin_source_build(
        compose, "langfuse-minio",
        _svc_defs(compose)["langfuse-minio"], tree) is True


def test_outside_root_rejected_even_with_builtin_true(tmp_path):
    """A byte-identical tree outside builtin_root must fail; builtin flag alone
    grants nothing."""
    rogue = tmp_path / "rogue" / "langfuse"
    rogue.parent.mkdir(parents=True)
    shutil.copytree(SHIPPED, rogue, symlinks=False)
    compose = rogue / "compose.yaml.disabled"
    assert verify_builtin_source_build(
        compose, "langfuse-minio",
        _svc_defs(compose)["langfuse-minio"], tmp_path / "not-root") is False


def test_builtin_false_never_grants_build_trust(tmp_path):
    """Non-builtin scan of a local build is rejected regardless of helper."""
    ext = tmp_path / "EXT"
    dest = ext / "langfuse"
    dest.parent.mkdir(parents=True)
    shutil.copytree(SHIPPED, dest, symlinks=False)
    with pytest.raises(HTTPException):
        extensions._scan_compose_content(
            dest / "compose.yaml.disabled",
            skip_name_collision=True, skip_gpu_passthrough_check=True,
            skip_root_user_check=True, builtin=False)


def test_symlinked_package_rejected(tmp_path):
    """Symlinked package dir must fail on ORIGINAL identity, pre-resolve."""
    real = tmp_path / "real" / "langfuse"
    real.parent.mkdir(parents=True)
    shutil.copytree(SHIPPED, real, symlinks=False)
    link = tmp_path / "EXTENSIONS_DIR"
    link.mkdir()
    (link / "langfuse").symlink_to(real)
    compose = link / "langfuse" / "compose.yaml.disabled"
    assert verify_builtin_source_build(
        compose, "langfuse-minio",
        _svc_defs(compose)["langfuse-minio"], link) is False


def test_symlinked_compose_rejected(tree, tmp_path):
    real = _compose(tree, "compose.yaml.disabled")
    fake = tree / "langfuse" / "compose.yaml"
    fake.unlink()
    fake.symlink_to(real)
    assert verify_builtin_source_build(
        fake, "langfuse-minio",
        _svc_defs(real)["langfuse-minio"], tree) is False


def test_symlinked_dockerfile_rejected(tree, tmp_path):
    compose = _compose(tree, "compose.yaml.disabled")
    outside = tmp_path / "outside.minio"
    shutil.copy(tree / "langfuse" / "Dockerfile.minio", outside)
    df = tree / "langfuse" / "Dockerfile.minio"
    df.unlink()
    df.symlink_to(outside)
    assert verify_builtin_source_build(
        compose, "langfuse-minio",
        _svc_defs(compose)["langfuse-minio"], tree) is False


def test_hardlinked_dockerfile_rejected(tree):
    compose = _compose(tree, "compose.yaml.disabled")
    df = tree / "langfuse" / "Dockerfile.minio"
    twin = tree / "langfuse" / "twin.minio"
    os.link(df, twin)
    assert verify_builtin_source_build(
        compose, "langfuse-minio",
        _svc_defs(compose)["langfuse-minio"], tree) is False
    twin.unlink()


def test_missing_and_oversized_dockerfile_rejected(tree):
    compose = _compose(tree, "compose.yaml.disabled")
    df = tree / "langfuse" / "Dockerfile.mc"
    svc = _svc_defs(compose)["langfuse-minio-init"]
    df.unlink()
    assert verify_builtin_source_build(compose, "langfuse-minio-init", svc, tree) is False
    df.write_bytes(b"# pad\n" * 6000)
    assert verify_builtin_source_build(compose, "langfuse-minio-init", svc, tree) is False


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(image="ods-langfuse-minio:WRONG"),
    lambda d: d["build"].update(context="./elsewhere"),
    lambda d: d.update(build="./extensions/services/langfuse"),
    lambda d: d["build"].update(args={"FOO": "bar"}),
    lambda d: d["build"].update(additional_contexts={"x": "./x"}),
])
def test_wrong_image_or_build_shape_rejected(tree, mutate):
    compose = _compose(tree, "compose.yaml.disabled")
    svc = dict(_svc_defs(compose)["langfuse-minio"])
    svc["build"] = dict(svc["build"])
    mutate(svc)
    assert verify_builtin_source_build(
        compose, "langfuse-minio", svc, tree) is False


@pytest.mark.parametrize("danger", [
    {"privileged": True},
    {"network_mode": "host"},
])
def test_shared_policy_still_rejects_after_helper_approval(tree, danger):
    """Helper approval clears only the source-recipe gate; the shared compose
    policy must still reject privileges/host networking/dangerous binds."""
    compose = _compose(tree, "compose.yaml.disabled")
    doc = yaml.safe_load(compose.read_text(encoding="utf-8"))
    doc["services"]["langfuse-minio"].update(danger)
    bad = tree / "langfuse" / "compose.yaml"
    bad.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(HTTPException):
        extensions._scan_compose_content(
            bad, skip_name_collision=True, skip_gpu_passthrough_check=True,
            skip_root_user_check=True, builtin=True)


def test_dangerous_bind_still_rejected_after_helper_approval(tree):
    compose = _compose(tree, "compose.yaml.disabled")
    doc = yaml.safe_load(compose.read_text(encoding="utf-8"))
    doc["services"]["langfuse-minio"]["volumes"] = ["/etc:/etc:ro"]
    bad = tree / "langfuse" / "compose.yaml"
    bad.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(HTTPException):
        extensions._scan_compose_content(
            bad, skip_name_collision=True, skip_gpu_passthrough_check=True,
            skip_root_user_check=True, builtin=True)

def _single_service(tree):
    compose = tree / "langfuse" / "compose.yaml.disabled"
    svc = _svc_defs(compose)["langfuse-minio"]
    compose.write_text(yaml.safe_dump({"services": {"langfuse-minio": svc}}))
    return compose, svc


def test_router_rejects_tampered_builtin_recipe(tree):
    compose, _ = _single_service(tree)
    recipe = tree / "langfuse" / "Dockerfile.minio"
    recipe.write_bytes(recipe.read_bytes() + b"\nRUN echo changed\n")
    with pytest.raises(HTTPException, match="verified source recipe"):
        extensions._scan_compose_content(compose, builtin=True)


def test_router_does_not_trust_builtin_flag_outside_custody(tree, tmp_path):
    compose, svc = _single_service(tree)
    rogue = tmp_path / "user" / "langfuse"
    shutil.copytree(compose.parent, rogue)
    assert not verify_builtin_source_build(rogue / compose.name, "langfuse-minio", svc, tree)
    with pytest.raises(HTTPException, match="verified source recipe"):
        extensions._scan_compose_content(rogue / compose.name, builtin=True)


def test_router_build_refusal_requires_builtin_custody(tree):
    compose, svc = _single_service(tree)
    assert verify_builtin_source_build(compose, "langfuse-minio", svc, tree)
    with pytest.raises(HTTPException, match="verified source recipe"):
        extensions._scan_compose_content(compose, builtin=False)


def test_symlinked_package_inside_root_is_rejected(tree):
    package = tree / "langfuse"
    other = tree / "reviewed-recipe-copy"
    package.rename(other)
    package.symlink_to(other, target_is_directory=True)
    compose = package / "compose.yaml.disabled"
    assert not verify_builtin_source_build(compose, "langfuse-minio", _svc_defs(compose)["langfuse-minio"], tree)


def test_symlinked_root_is_rejected(tree, tmp_path):
    link = tmp_path / "root-link"
    link.symlink_to(tree, target_is_directory=True)
    compose = link / "langfuse" / "compose.yaml.disabled"
    assert not verify_builtin_source_build(compose, "langfuse-minio", _svc_defs(compose)["langfuse-minio"], link)


def test_unreviewed_compose_name_is_rejected(tree):
    compose, svc = _single_service(tree)
    other = compose.with_name("compose.other.yaml")
    shutil.copyfile(compose, other)
    assert not verify_builtin_source_build(other, "langfuse-minio", svc, tree)


def test_crlf_checkout_keeps_reviewed_recipe_hash(tree):
    compose, svc = _single_service(tree)
    recipe = tree / "langfuse" / "Dockerfile.minio"
    recipe.write_bytes(recipe.read_bytes().replace(b"\n", b"\r\n"))
    assert verify_builtin_source_build(compose, "langfuse-minio", svc, tree)


def test_nonregular_recipe_is_rejected_without_blocking(tree):
    compose, svc = _single_service(tree)
    recipe = tree / "langfuse" / "Dockerfile.minio"
    recipe.unlink()
    os.mkfifo(recipe)
    assert not verify_builtin_source_build(compose, "langfuse-minio", svc, tree)
