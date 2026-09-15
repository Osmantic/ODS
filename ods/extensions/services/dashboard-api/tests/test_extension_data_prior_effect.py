"""Linux effect-time proof that approved prior data still names the installed manifest."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_data_prior_effect as effect  # noqa: E402
import extension_data_scope_contract as scope  # noqa: E402
from extension_document_digest import canonical_document_sha256  # noqa: E402
from extension_lifecycle_work import LifecycleWorkValidationError  # noqa: E402
from test_extension_data_scope_contract import command  # noqa: E402


linux_effect = pytest.mark.skipif(os.name != "posix", reason="descriptor-relative Linux effect")

PRIOR_MANIFEST = b"""schema_version: ods.services.v2
service:
  id: beta
  version: "1.0.0"
  data_schema_version: "1"
  planning:
    data:
      - path: data/old
        backup_class: required
        owner: user
        uninstall: preserve
        purge: separate-approval
"""


def _bound(raw: bytes = PRIOR_MANIFEST):
    value = command()
    old = replace(
        value.plan_material.prior_data_bindings[0],
        definition_sha256=canonical_document_sha256(raw),
    )
    material = replace(value.plan_material, prior_data_bindings=(old,))
    return scope.bind_data_scope(replace(value, plan_material=material))


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    install = tmp_path / "install"
    data = tmp_path / "data"
    builtin = install / "extensions" / "services" / "beta"
    builtin.mkdir(parents=True)
    data.mkdir()
    for directory in (install, install / "extensions", install / "extensions" / "services", builtin, data):
        directory.chmod(0o700)
    return install, data, builtin


def _rejected(bound, install: Path, data: Path) -> None:
    with pytest.raises(LifecycleWorkValidationError) as caught:
        effect.verify_installed_prior_data(bound, install_dir=install, data_dir=data)
    assert caught.value.code == "lifecycle-work-prior-data-drift"


def _write_manifest(path: Path, raw: bytes) -> None:
    path.write_bytes(raw)
    path.chmod(0o600)


def test_prior_document_digest_and_old_records_match_exact_binding():
    service = _bound().services[1]
    assert effect._check_manifest(PRIOR_MANIFEST, service) == canonical_document_sha256(PRIOR_MANIFEST)
    with pytest.raises(LifecycleWorkValidationError) as caught:
        effect._check_manifest(PRIOR_MANIFEST.replace(b"data/old", b"data/new"), service)
    assert caught.value.code == "lifecycle-work-prior-data-drift"


@linux_effect
def test_actual_installed_prior_manifest_matches_approved_old_scope(tmp_path: Path):
    install, data, builtin = _roots(tmp_path)
    _write_manifest(builtin / "manifest.yaml", PRIOR_MANIFEST)
    assert effect.verify_installed_prior_data(
        _bound(), install_dir=install, data_dir=data
    ) == (("beta", canonical_document_sha256(PRIOR_MANIFEST)),)


@linux_effect
def test_manifest_changes_after_approval_are_not_catalog_substituted(tmp_path: Path):
    install, data, builtin = _roots(tmp_path)
    _write_manifest(builtin / "manifest.yaml", PRIOR_MANIFEST.replace(b"data/old", b"data/new"))
    _rejected(_bound(), install, data)


@linux_effect
def test_ambiguous_user_and_builtin_roots_are_rejected(tmp_path: Path):
    install, data, builtin = _roots(tmp_path)
    _write_manifest(builtin / "manifest.yaml", PRIOR_MANIFEST)
    user = data / "user-extensions" / "beta"
    user.mkdir(parents=True)
    (data / "user-extensions").chmod(0o700)
    user.chmod(0o700)
    _write_manifest(user / "manifest.yaml", PRIOR_MANIFEST)
    _rejected(_bound(), install, data)


@linux_effect
def test_manifest_symlink_and_special_file_are_rejected(tmp_path: Path):
    install, data, builtin = _roots(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_bytes(PRIOR_MANIFEST)
    (builtin / "manifest.yaml").symlink_to(outside)
    _rejected(_bound(), install, data)


@linux_effect
def test_restore_does_not_recheck_manifest_already_replaced_by_update(tmp_path: Path):
    install, data, builtin = _roots(tmp_path)
    _write_manifest(builtin / "manifest.yaml", PRIOR_MANIFEST)
    rejected = replace(_bound(), operation_key="restore")
    _rejected(rejected, install, data)


@linux_effect
def test_absolute_root_with_parent_traversal_is_rejected():
    with pytest.raises(LifecycleWorkValidationError) as caught:
        effect._open_directory(Path("/tmp/../../etc"))
    assert caught.value.code == "lifecycle-work-prior-data-drift"
