"""Custody and retry behavior for the ODS-owned runtime repair installer."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

if os.name != "posix":
    pytest.skip("managed OpenClaw host repair uses POSIX ownership", allow_module_level=True)

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("openclaw_tool_recovery", ROOT / "host/openclaw_tool_recovery.py")
repair_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair_module)


@pytest.fixture
def installation(tmp_path):
    runtime = tmp_path / "runtime"
    (runtime / "dist").mkdir(parents=True)
    (runtime / "package.json").write_text(json.dumps({"name": "openclaw", "version": repair_module.VERSION}))
    original = b"before();\nunchanged();\n"
    patched = b"after();\nunchanged();\n"
    module = runtime / "dist" / repair_module.MODULE
    module.write_bytes(original)
    module.chmod(0o644)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"sourceSha256": hashlib.sha256(original).hexdigest(),
                                    "patchedSha256": hashlib.sha256(patched).hexdigest(),
                                    "replacements": [["before();", "after();"]]}))
    state = tmp_path / "state"
    return runtime, state, manifest, module, original, patched


def run(installation, **kwargs):
    runtime, state, manifest, *_ = installation
    return repair_module.repair(runtime, state, manifest_path=manifest, **kwargs)


@pytest.mark.parametrize("module_name", [repair_module.COMPLETION_MODULE, repair_module.IMAGE_MODULE])
def test_additional_module_has_separate_exact_byte_custody(installation, module_name):
    runtime, state, manifest, module, original, patched = installation
    completion = module.with_name(module_name)
    module.rename(completion)
    outcome = run(installation, module_name=module_name)
    assert outcome["module"] == module_name
    assert completion.read_bytes() == patched
    run(installation, module_name=module_name, restore=True)
    assert completion.read_bytes() == original
    assert not module.exists()


def test_unrecognized_module_cannot_expand_repair_targets(installation):
    with pytest.raises(ValueError, match="unsupported runtime repair module"):
        run(installation, module_name="../other-file.js")
    assert not installation[1].exists()


def test_apply_reapply_restore_preserve_bytes_permissions_and_backup(installation):
    _, state, _, module, original, patched = installation
    assert run(installation)["status"] == "changed"
    assert module.read_bytes() == patched
    assert module.stat().st_mode & 0o777 == 0o644
    backup = next(state.glob("*.js"))
    assert backup.read_bytes() == original
    assert backup.stat().st_mode & 0o777 == 0o600
    assert run(installation)["status"] == "unchanged"
    assert run(installation, restore=True)["status"] == "changed"
    assert module.read_bytes() == original
    assert run(installation, restore=True)["status"] == "unchanged"


def test_later_runtime_changes_are_not_overwritten_by_restore(installation):
    run(installation)
    module = installation[3]
    module.write_bytes(b"independent package update")
    with pytest.raises(ValueError, match="differs from reviewed"):
        run(installation, restore=True)
    assert module.read_bytes() == b"independent package update"


def test_exact_previous_patch_upgrades_and_restores_to_original(installation):
    _, state, manifest_path, module, original, previous = installation
    run(installation)
    candidate = previous.replace(b"after();", b"latest();")
    manifest = json.loads(manifest_path.read_text())
    manifest["previousReplacements"] = {manifest["patchedSha256"]: manifest["replacements"]}
    manifest["patchedSha256"] = hashlib.sha256(candidate).hexdigest()
    manifest["replacements"] = [["before();", "latest();"]]
    manifest_path.write_text(json.dumps(manifest))
    assert run(installation)["status"] == "changed"
    assert module.read_bytes() == candidate
    assert next(state.glob("*.js")).read_bytes() == original
    assert run(installation)["status"] == "unchanged"
    assert run(installation, restore=True)["status"] == "changed"
    assert module.read_bytes() == original


def test_predecessor_recipe_must_reconstruct_exact_original_before_writing(installation):
    _, _, manifest_path, module, _, previous = installation
    run(installation)
    manifest = json.loads(manifest_path.read_text())
    manifest["previousReplacements"] = {manifest["patchedSha256"]: [["wrong();", "after();"]]}
    manifest["patchedSha256"] = hashlib.sha256(b"latest();\nunchanged();\n").hexdigest()
    manifest["replacements"] = [["before();", "latest();"]]
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="baseline hash mismatch"):
        run(installation)
    assert module.read_bytes() == previous


def test_normal_npm_group_mode_is_preserved(installation):
    module = installation[3]
    module.chmod(0o664)
    assert run(installation)["status"] == "changed"
    assert module.stat().st_mode & 0o777 == 0o664
    assert module.read_bytes() == installation[5]


def test_changed_backup_is_not_used(installation):
    run(installation)
    next(installation[1].glob("*.js")).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="backup hash mismatch"):
        run(installation, restore=True)
    assert installation[3].read_bytes() == installation[5]


def test_interrupted_patched_state_reconstructs_only_reviewed_backup(installation):
    installation[3].write_bytes(installation[5])
    assert run(installation)["status"] == "unchanged"
    assert next(installation[1].glob("*.js")).read_bytes() == installation[4]
    assert json.loads((installation[1] / "receipt.json").read_text())["desiredSha256"] == hashlib.sha256(installation[5]).hexdigest()


def test_source_symlink_and_shared_writes_rejected(installation, tmp_path):
    module = installation[3]
    target = tmp_path / "elsewhere"
    target.write_bytes(installation[4])
    module.unlink()
    module.symlink_to(target)
    with pytest.raises(ValueError, match="owner-controlled"):
        run(installation)
    assert target.read_bytes() == installation[4]
    module.unlink()
    module.write_bytes(installation[4])
    module.chmod(0o666)
    with pytest.raises(ValueError, match="owner-controlled"):
        run(installation)


def test_failed_atomic_replace_leaves_original_and_retryable_custody(installation, monkeypatch):
    replace = repair_module.os.replace
    module = installation[3]

    def fail_module(source, destination):
        if Path(destination) == module:
            raise OSError("simulated write failure")
        return replace(source, destination)

    monkeypatch.setattr(repair_module.os, "replace", fail_module)
    with pytest.raises(OSError, match="simulated"):
        run(installation)
    assert module.read_bytes() == installation[4]
    assert next(installation[1].glob("*.js")).read_bytes() == installation[4]
    assert list(module.parent.glob(".ods-repair-*")) == []
    monkeypatch.setattr(repair_module.os, "replace", replace)
    assert run(installation)["status"] == "changed"


def test_other_runtime_versions_are_untouched(installation):
    runtime = installation[0]
    (runtime / "package.json").write_text(json.dumps({"name": "openclaw", "version": "future"}))
    assert run(installation) == {"status": "not-applicable", "version": "future"}
    assert installation[3].read_bytes() == installation[4]
    assert not installation[1].exists()


@pytest.fixture
def compaction_installation(installation):
    runtime, state, manifest, module, original, patched = installation
    facade = module.with_name(repair_module.COMPACTION_MODULE)
    module.rename(facade)
    chunk = facade.with_name(repair_module.COMPACTION_CHUNK)
    chunk.write_bytes(b"export default async function reconcile() {}\n")
    chunk.chmod(0o644)
    data = json.loads(manifest.read_text())
    data["reviewedDependencies"] = {chunk.name: hashlib.sha256(chunk.read_bytes()).hexdigest()}
    manifest.write_text(json.dumps(data))
    return runtime, state, manifest, facade, original, patched


def compact(installation, **kwargs):
    return run(installation, module_name=repair_module.COMPACTION_MODULE, **kwargs)


def test_compaction_repair_retains_dependency_custody(compaction_installation):
    runtime, state, manifest, facade, original, patched = compaction_installation
    expected = json.loads(manifest.read_text())["reviewedDependencies"]
    outcome = compact(compaction_installation)
    assert outcome["reviewedDependencies"] == expected
    assert json.loads((state / "receipt.json").read_text())["reviewedDependencies"] == expected
    assert facade.read_bytes() == patched
    assert compact(compaction_installation)["status"] == "unchanged"
    assert compact(compaction_installation, restore=True)["status"] == "changed"
    assert facade.read_bytes() == original


@pytest.mark.parametrize("restore", [False, True])
def test_compaction_dependency_drift_cannot_apply_or_restore(compaction_installation, restore):
    runtime, _, _, facade, original, patched = compaction_installation
    if restore:
        compact(compaction_installation)
    chunk = runtime / "dist" / repair_module.COMPACTION_CHUNK
    chunk.write_bytes(b"an independent package update")
    with pytest.raises(ValueError, match="dependency differs"):
        compact(compaction_installation, restore=restore)
    assert facade.read_bytes() == (patched if restore else original)
    assert chunk.read_bytes() == b"an independent package update"


@pytest.mark.parametrize("dependencies", [{}, {"../outside.js": "a" * 64}])
def test_compaction_dependency_contract_cannot_expand_targets(compaction_installation, dependencies):
    _, state, manifest, facade, original, _ = compaction_installation
    data = json.loads(manifest.read_text())
    data["reviewedDependencies"] = dependencies
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="dependency contract"):
        compact(compaction_installation)
    assert facade.read_bytes() == original
    assert not (state / "receipt.json").exists()


def test_compaction_dependency_symlink_is_rejected(compaction_installation, tmp_path):
    runtime, _, _, facade, original, _ = compaction_installation
    chunk = runtime / "dist" / repair_module.COMPACTION_CHUNK
    other = tmp_path / "other.js"
    chunk.rename(other)
    chunk.symlink_to(other)
    with pytest.raises(ValueError, match="owner-controlled"):
        compact(compaction_installation)
    assert facade.read_bytes() == original


def test_compaction_dependency_drift_during_preparation_stops_write(compaction_installation, monkeypatch):
    runtime, _, _, facade, original, _ = compaction_installation
    chunk = runtime / "dist" / repair_module.COMPACTION_CHUNK
    write = repair_module.atomic_write

    def change_chunk_after_receipt(path, *args, **kwargs):
        write(path, *args, **kwargs)
        if path.name == "receipt.json":
            chunk.write_bytes(b"updated during preparation")

    monkeypatch.setattr(repair_module, "atomic_write", change_chunk_after_receipt)
    with pytest.raises(ValueError, match="dependency differs"):
        compact(compaction_installation)
    assert facade.read_bytes() == original


def test_unchanged_compaction_repair_checks_its_dependency(compaction_installation):
    runtime, _, _, facade, _, patched = compaction_installation
    compact(compaction_installation)
    (runtime / "dist" / repair_module.COMPACTION_CHUNK).unlink()
    with pytest.raises(FileNotFoundError):
        compact(compaction_installation)
    assert facade.read_bytes() == patched
