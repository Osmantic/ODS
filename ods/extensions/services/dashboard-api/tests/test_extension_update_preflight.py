from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import assistant_first_planner as planner
import extension_lockfile as lockfile
import extension_update_preflight as preflight
from extension_planning_contract import computed_catalog_revision


REVISION = "a" * 40


def _lockfile_envelope(
    ods_version: str = "2.6.0", *, extensions: list[dict] | None = None
) -> dict:
    document = {
        "schema": "ods.extensions.lockfile.v1",
        "odsVersion": ods_version,
        "catalogRevision": "b" * 64,
        "platform": "linux",
        "architecture": "amd64",
        "containerRuntime": "docker",
        "runtimeMode": "assistant-first",
        "postCommitObservedStateRevision": "c" * 64,
        "extensions": [] if extensions is None else extensions,
        "lastCommittedTransaction": {
            "transactionId": "txn-" + "d" * 24,
            "planHash": "e" * 64,
            "sequence": 1,
            "plannedObservedStateRevision": "f" * 64,
        },
        "priorLockfileHash": None,
        "backupReference": "backup-v1-20260912T200000Z",
    }
    return lockfile.lockfile_envelope(document)


def _write_lockfile(
    install_dir: Path,
    *,
    canonical: bool = True,
    ods_version: str = "2.6.0",
    extensions: list[dict] | None = None,
) -> Path:
    root = install_dir / "data/assistant-first/desired-state"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "extensions.lock.json"
    envelope = _lockfile_envelope(ods_version, extensions=extensions)
    if canonical:
        path.write_bytes(lockfile.canonical_lockfile_bytes(envelope))
    else:
        path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    if os.name == "posix":
        root.chmod(0o700)
        path.chmod(0o600)
    return path


def _candidate(candidate_dir: Path) -> tuple[bytes, bytes]:
    repository_ods = Path(__file__).resolve().parents[4]
    manifest = (repository_ods / "manifest.json").read_bytes()
    catalog = (repository_ods / "config/extensions-catalog.json").read_bytes()
    (candidate_dir / "config").mkdir(parents=True)
    (candidate_dir / "manifest.json").write_bytes(manifest)
    (candidate_dir / "config/extensions-catalog.json").write_bytes(catalog)
    return manifest, catalog


def _locked_extension(
    service_id: str,
    *,
    version: str = "1.2.3",
    maximum: str | None = "2.5.9",
) -> dict:
    return {
        "id": service_id,
        "desiredState": "enabled",
        "version": version,
        "manifestSchemaVersion": "ods.services.v2",
        "dataSchemaVersion": "1",
        "odsCompatibility": {"minimum": "2.0.0", "maximum": maximum},
        "definitionHashes": {
            "manifestSha256": "sha256:" + "d" * 64,
            "composeSha256": "sha256:" + "c" * 64,
        },
        "imageDigests": ["sha256:" + "e" * 64],
        "dependencyEdges": [],
        "configuration": {
            "schemaSha256": "f" * 64,
            "presentConfigKeys": [],
            "presentSecretKeys": [],
            "secretReference": None,
        },
    }


def _candidate_catalog_entry(service_id: str, *, version: str = "2.0.0") -> dict:
    return {
        "id": service_id,
        "manifest_schema_version": "ods.services.v2",
        "planning": {
            "serviceType": "docker",
            "version": version,
            "dataSchemaVersion": "1",
            "odsCompatibility": {"minimum": "2.0.0", "maximum": "3.5.0"},
            "definitionSha256": "sha256:" + "b" * 64,
            "composeSha256": "sha256:" + "c" * 64,
            "dependsOn": [],
            "provides": [],
            "requires": [],
            "optional": [],
            "conflicts": [],
            "providerPriority": 0,
            "requirements": {
                "platforms": ["linux"],
                "architectures": ["amd64"],
                "containerRuntimes": ["docker"],
                "gpuBackends": ["cpu"],
                "minDriverVersion": None,
            },
            "estimates": {
                "downloadBytes": 100,
                "diskBytes": 200,
                "cpuMillicores": 100,
                "ramBytes": 300,
                "vramBytes": 0,
                "gpuCount": 0,
            },
            "resources": {
                "hostPorts": [],
                "containerPorts": [],
                "networks": [],
                "volumes": [],
                "devices": [],
                "exclusive": [],
                "linuxCapabilities": [],
                "hostPermissions": ["network"],
            },
            "configuration": [],
            "artifacts": {
                "images": [
                    {
                        "reference": f"example/{service_id}:{version}",
                        "digest": "sha256:" + "e" * 64,
                        "downloadBytes": 100,
                    }
                ],
                "builds": [],
            },
            "lifecycle": {
                "healthChecks": ["http://127.0.0.1/health"],
                "readiness": ["healthy"],
                "setupHook": None,
                "migrationHook": None,
                "rollback": "definition",
                "timeoutSeconds": 120,
            },
            "data": [],
            "trust": {
                "tier": "bundled",
                "publisher": "ODS",
                "definitionSignature": None,
            },
            "support": {"status": "supported", "url": None},
            "legacy": False,
        },
    }


def _write_candidate_catalog(candidate_dir: Path, entries: list[dict]) -> bytes:
    document = {
        "catalog_revision": computed_catalog_revision(entries),
        "extensions": entries,
    }
    raw = planner.canonical_json_bytes(document)
    path = candidate_dir / "config/extensions-catalog.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _fixture(tmp_path: Path) -> tuple[Path, Path, bytes, bytes]:
    install_dir = tmp_path / "installed"
    candidate_dir = tmp_path / "candidate"
    install_dir.mkdir()
    candidate_dir.mkdir()
    _write_lockfile(install_dir)
    (install_dir / ".env").write_text("ODS_VERSION=2.6.0\n", encoding="utf-8")
    manifest, catalog = _candidate(candidate_dir)
    return install_dir, candidate_dir, manifest, catalog


def test_exact_candidate_tree_is_bound_and_deterministic(tmp_path: Path) -> None:
    install_dir, candidate_dir, manifest, catalog = _fixture(tmp_path)

    first = preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)
    second = preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)

    assert planner.canonical_json_bytes(first) == planner.canonical_json_bytes(second)
    assert first["schema"] == "ods.extensions.update-preflight.v1"
    assert first["candidateSourceRevision"] == REVISION
    assert first["candidateManifestSha256"] == hashlib.sha256(manifest).hexdigest()
    assert first["candidateCatalogFileSha256"] == hashlib.sha256(catalog).hexdigest()
    assert first["assessmentEnvelope"]["assessment"]["canUpdate"] is True
    assert first["assessmentEnvelope"]["assessment"]["requiresExtensionPlan"] is False
    material = {key: value for key, value in first.items() if key != "preflightHash"}
    assert (
        first["preflightHash"]
        == hashlib.sha256(planner.canonical_json_bytes(material)).hexdigest()
    )


def test_preflight_reads_without_mutating_either_tree(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)

    def inventory(root: Path) -> list[tuple[str, bytes]]:
        return sorted(
            (path.relative_to(root).as_posix(), path.read_bytes())
            for path in root.rglob("*")
            if path.is_file()
        )

    before = (inventory(install_dir), inventory(candidate_dir))
    preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)
    after = (inventory(install_dir), inventory(candidate_dir))

    assert after == before


def test_installed_version_precedence_and_normalization(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    (install_dir / ".env").write_text('ODS_VERSION="v2.6.0"\n', encoding="utf-8")
    (install_dir / ".version").write_text(
        json.dumps({"version": "9.9.9"}), encoding="utf-8"
    )

    result = preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)

    assert result["assessmentEnvelope"]["assessment"]["installedOdsVersion"] == "2.6.0"


def test_version_file_and_manifest_fallbacks(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    (install_dir / ".env").unlink()
    (install_dir / ".version").write_text('{"version":"v2.6.0"}', encoding="utf-8")
    assert preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)

    (install_dir / ".version").unlink()
    (install_dir / "manifest.json").write_bytes(
        (candidate_dir / "manifest.json").read_bytes()
    )
    assert preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)


def test_real_compatibility_blocker_uses_blocked_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    service_id = "phase5d2-missing-extension"
    _write_lockfile(
        install_dir,
        extensions=[_locked_extension(service_id, maximum="3.5.0")],
    )

    code = preflight.main(
        [
            "--install-dir",
            str(install_dir),
            "--candidate-dir",
            str(candidate_dir),
            "--candidate-revision",
            REVISION,
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == preflight.EXIT_BLOCKED
    assessment = output["assessmentEnvelope"]["assessment"]
    assert assessment["canUpdate"] is False
    assert assessment["requiresExtensionPlan"] is False
    assert assessment["blockers"] == [
        {"code": "candidate-extension-missing", "serviceId": service_id}
    ]


def test_real_required_upgrade_uses_extension_plan_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    service_id = "phase5d2-upgrade"
    _write_lockfile(
        install_dir,
        extensions=[_locked_extension(service_id)],
    )
    _write_candidate_catalog(candidate_dir, [_candidate_catalog_entry(service_id)])

    code = preflight.main(
        [
            "--install-dir",
            str(install_dir),
            "--candidate-dir",
            str(candidate_dir),
            "--candidate-revision",
            REVISION,
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == preflight.EXIT_EXTENSION_PLAN_REQUIRED
    assessment = output["assessmentEnvelope"]["assessment"]
    assert assessment["canUpdate"] is True
    assert assessment["requiresExtensionPlan"] is True
    assert assessment["requiredUpgrades"] == [
        {
            "serviceId": service_id,
            "fromVersion": "1.2.3",
            "toVersion": "2.0.0",
            "definitionHashes": {
                "manifestSha256": "sha256:" + "b" * 64,
                "composeSha256": "sha256:" + "c" * 64,
            },
            "imageDigests": ["sha256:" + "e" * 64],
        }
    ]


def test_candidate_ods_version_regression_surfaces_bounded_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    manifest_path = candidate_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["ods_version"] = "2.5.9"
    manifest["release"]["version"] = "2.5.9"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    code = preflight.main(
        [
            "--install-dir",
            str(install_dir),
            "--candidate-dir",
            str(candidate_dir),
            "--candidate-revision",
            REVISION,
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == preflight.EXIT_INVALID_INPUT
    assert output == {
        "schema": preflight.PREFLIGHT_ERROR_SCHEMA,
        "error": {"code": "candidate-ods-version-regression", "details": {}},
    }


def test_real_extension_version_regression_uses_blocked_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    service_id = "phase5d2-extension-regression"
    _write_lockfile(
        install_dir,
        extensions=[
            _locked_extension(service_id, version="2.0.0", maximum="3.5.0")
        ],
    )
    _write_candidate_catalog(
        candidate_dir,
        [_candidate_catalog_entry(service_id, version="1.0.0")],
    )

    code = preflight.main(
        [
            "--install-dir",
            str(install_dir),
            "--candidate-dir",
            str(candidate_dir),
            "--candidate-revision",
            REVISION,
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == preflight.EXIT_BLOCKED
    assessment = output["assessmentEnvelope"]["assessment"]
    assert assessment["canUpdate"] is False
    assert assessment["requiresExtensionPlan"] is False
    assert assessment["blockers"] == [
        {"code": "candidate-extension-version-regression", "serviceId": service_id}
    ]


@pytest.mark.parametrize(
    ("revision", "code"),
    [
        ("A" * 40, "invalid-candidate-revision"),
        ("a" * 39, "invalid-candidate-revision"),
    ],
)
def test_candidate_revision_must_be_an_exact_lowercase_object_id(
    tmp_path: Path, revision: str, code: str
) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)

    with pytest.raises(preflight.ExtensionUpdatePreflightError, match=code):
        preflight.assess_candidate_tree(install_dir, candidate_dir, revision)


def test_candidate_manifest_versions_must_agree(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    manifest_path = candidate_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release"]["version"] = "2.6.1"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        preflight.ExtensionUpdatePreflightError, match="manifest-version-mismatch"
    ):
        preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)


def test_duplicate_version_or_json_keys_fail_closed(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    (install_dir / ".env").write_text(
        "ODS_VERSION=2.6.0\nODS_VERSION=2.6.0\n", encoding="utf-8"
    )
    with pytest.raises(
        preflight.ExtensionUpdatePreflightError, match="duplicate-installed-version"
    ):
        preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)

    (install_dir / ".env").write_text("ODS_VERSION=2.6.0\n", encoding="utf-8")
    (candidate_dir / "manifest.json").write_text(
        '{"ods_version":"2.6.0","ods_version":"2.6.0"}', encoding="utf-8"
    )
    with pytest.raises(
        preflight.ExtensionUpdatePreflightError, match="json-duplicate-key"
    ):
        preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)


def test_noncanonical_lockfile_is_rejected(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    _write_lockfile(install_dir, canonical=False)

    with pytest.raises(
        preflight.ExtensionUpdatePreflightError,
        match="source-lockfile-noncanonical",
    ):
        preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)


def test_symlinked_candidate_file_is_rejected(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    catalog_path = candidate_dir / "config/extensions-catalog.json"
    target = candidate_dir / "catalog-target.json"
    target.write_bytes(catalog_path.read_bytes())
    catalog_path.unlink()
    try:
        catalog_path.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(preflight.ExtensionUpdatePreflightError, match="file-symlink"):
        preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)


def test_hardlinked_candidate_file_is_rejected(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    manifest_path = candidate_dir / "manifest.json"
    target = candidate_dir / "manifest-target.json"
    target.write_bytes(manifest_path.read_bytes())
    manifest_path.unlink()
    try:
        os.link(target, manifest_path)
    except OSError:
        pytest.skip("hardlink creation is unavailable")

    with pytest.raises(preflight.ExtensionUpdatePreflightError, match="unsafe-file"):
        preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)


def test_nonregular_candidate_file_is_rejected(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    manifest_path = candidate_dir / "manifest.json"
    manifest_path.unlink()
    manifest_path.mkdir()

    with pytest.raises(preflight.ExtensionUpdatePreflightError, match="unsafe-file"):
        preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)


def test_oversized_candidate_file_is_rejected(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    catalog_path = candidate_dir / "config/extensions-catalog.json"
    catalog_path.write_bytes(b" " * (4 * 1024 * 1024 + 1))

    with pytest.raises(
        preflight.ExtensionUpdatePreflightError, match="file-too-large"
    ):
        preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor identity contract")
def test_candidate_file_replacement_during_open_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    manifest_path = candidate_dir / "manifest.json"
    replacement = candidate_dir / "replacement.json"
    replacement.write_bytes(manifest_path.read_bytes())
    original_open = preflight.os.open

    def swapped_open(path: Path, flags: int) -> int:
        if Path(path) == manifest_path:
            return original_open(replacement, flags)
        return original_open(path, flags)

    monkeypatch.setattr(preflight.os, "open", swapped_open)

    with pytest.raises(preflight.ExtensionUpdatePreflightError, match="file-replaced"):
        preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)


def test_symlinked_root_component_is_rejected(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    linked = tmp_path / "linked-candidate"
    try:
        linked.symlink_to(candidate_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(
        preflight.ExtensionUpdatePreflightError, match="root-component-symlink"
    ):
        preflight.assess_candidate_tree(install_dir, linked, REVISION)


def test_cli_errors_are_bounded_and_do_not_expose_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install_dir = tmp_path / "private-install-name"
    candidate_dir = tmp_path / "private-candidate-name"
    install_dir.mkdir()
    candidate_dir.mkdir()

    code = preflight.main(
        [
            "--install-dir",
            str(install_dir),
            "--candidate-dir",
            str(candidate_dir),
            "--candidate-revision",
            REVISION,
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert code == preflight.EXIT_INVALID_INPUT
    assert output == {
        "schema": "ods.extensions.update-preflight-error.v1",
        "error": {
            "code": "file-missing",
            "details": {"field": "installed.lockfile"},
        },
    }
    assert "private-install-name" not in json.dumps(output)
    assert "private-candidate-name" not in json.dumps(output)


def test_installed_cli_wrapper_emits_the_bound_preflight(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    repository_ods = Path(__file__).resolve().parents[4]

    completed = subprocess.run(
        [
            sys.executable,
            str(repository_ods / "scripts/assess-extension-update.py"),
            "--install-dir",
            str(install_dir),
            "--candidate-dir",
            str(candidate_dir),
            "--candidate-revision",
            REVISION,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == preflight.EXIT_READY
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["schema"] == preflight.PREFLIGHT_SCHEMA


@pytest.mark.skipif(os.name != "posix", reason="POSIX custody contract")
def test_lockfile_must_retain_owner_only_custody(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    lock_path = install_dir / "data/assistant-first/desired-state/extensions.lock.json"
    lock_path.chmod(0o644)

    with pytest.raises(preflight.ExtensionUpdatePreflightError, match="file-custody"):
        preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)


@pytest.mark.skipif(os.name != "posix", reason="POSIX custody contract")
def test_lockfile_parent_must_retain_owner_only_custody(tmp_path: Path) -> None:
    install_dir, candidate_dir, _, _ = _fixture(tmp_path)
    lock_path = install_dir / "data/assistant-first/desired-state/extensions.lock.json"
    lock_path.parent.chmod(0o755)

    with pytest.raises(
        preflight.ExtensionUpdatePreflightError, match="file-parent-custody"
    ):
        preflight.assess_candidate_tree(install_dir, candidate_dir, REVISION)


@pytest.mark.parametrize(
    ("can_update", "requires_plan", "expected"),
    [
        (True, False, preflight.EXIT_READY),
        (True, True, preflight.EXIT_EXTENSION_PLAN_REQUIRED),
        (False, False, preflight.EXIT_BLOCKED),
    ],
)
def test_exit_codes_are_stable(
    can_update: bool, requires_plan: bool, expected: int
) -> None:
    result = {
        "assessmentEnvelope": {
            "assessment": {
                "canUpdate": can_update,
                "requiresExtensionPlan": requires_plan,
            }
        }
    }

    assert preflight.exit_code_for_preflight(result) == expected
