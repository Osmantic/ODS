"""Custody and retry behavior for the ODS-owned runtime repair installer."""


import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

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


@pytest.mark.parametrize("module_name", [repair_module.COMPLETION_MODULE, repair_module.IMAGE_MODULE,
                                         repair_module.COMPACTION_IDLE_MODULE, repair_module.COMPACTION_BUDGET_MODULE])
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


def apply_other_build(installation, tmp_path):
    """Apply a newer recipe this build does not know through the same custody."""
    runtime, state, manifest_path, module, original, _ = installation
    other = original.replace(b"before();", b"newer();")
    manifest = json.loads(manifest_path.read_text())
    manifest.update(patchedSha256=hashlib.sha256(other).hexdigest(),
                    replacements=[["before();", "newer();"]])
    other_manifest = tmp_path / "other-build.json"
    other_manifest.write_text(json.dumps(manifest))
    repair_module.repair(runtime, state, manifest_path=other_manifest)
    assert module.read_bytes() == other
    return other


@pytest.mark.parametrize("restore", [False, True])
def test_bytes_recorded_by_another_build_rebuild_from_verified_backup(installation, tmp_path, restore):
    _, state, _, module, original, patched = installation
    other = apply_other_build(installation, tmp_path)
    outcome = run(installation, restore=restore)
    assert outcome["status"] == "changed"
    assert module.read_bytes() == (original if restore else patched)
    receipt = json.loads((state / "receipt.json").read_text())
    assert receipt["recoveredFrom"] == outcome["recoveredFrom"] == "verified-backup"
    assert receipt["recoveredSha256"] == hashlib.sha256(other).hexdigest()
    assert next(state.glob("*.js")).read_bytes() == original
    assert run(installation, restore=restore)["status"] == "unchanged"


def test_interrupted_recovery_resumes_from_its_own_receipt(installation, tmp_path, monkeypatch):
    module, patched = installation[3], installation[5]
    other = apply_other_build(installation, tmp_path)
    replace = repair_module.os.replace

    def fail_module(source, destination):
        if Path(destination) == module:
            raise OSError("simulated write failure")
        return replace(source, destination)

    monkeypatch.setattr(repair_module.os, "replace", fail_module)
    with pytest.raises(OSError, match="simulated"):
        run(installation)
    assert module.read_bytes() == other
    monkeypatch.setattr(repair_module.os, "replace", replace)
    assert run(installation)["status"] == "changed"
    assert module.read_bytes() == patched


@pytest.mark.parametrize("damage,error", [
    ("missing-backup", "differs from reviewed"),
    ("tampered-backup", "backup hash mismatch"),
    ("missing-receipt", "differs from reviewed"),
    ("unrecorded-bytes", "differs from reviewed"),
    ("other-module", "differs from reviewed"),
])
def test_unknown_bytes_without_verified_custody_fail_closed(installation, tmp_path, damage, error):
    _, state, _, module, _, _ = installation
    other = apply_other_build(installation, tmp_path)
    backup = next(state.glob("*.js"))
    receipt_path = state / "receipt.json"
    if damage == "missing-backup":
        backup.unlink()
    elif damage == "tampered-backup":
        backup.write_bytes(b"tampered")
    elif damage == "missing-receipt":
        receipt_path.unlink()
    elif damage == "unrecorded-bytes":
        other = other + b"independent();\n"
        module.write_bytes(other)
    else:
        receipt = json.loads(receipt_path.read_text())
        receipt["module"] = repair_module.COMPLETION_MODULE
        receipt_path.write_text(json.dumps(receipt))
    receipt_before = receipt_path.read_bytes() if receipt_path.exists() else None
    with pytest.raises(ValueError, match=error):
        run(installation)
    assert module.read_bytes() == other
    assert (receipt_path.read_bytes() if receipt_path.exists() else None) == receipt_before


FOREIGN_MODULE = "agent-tools-D1DOpg6D.js"


def write_foreign_set(runtime, root, name, *, module_name=FOREIGN_MODULE, live="patched"):
    """Mirror the custody an ODS build with an unknown patch set leaves."""
    original = f"// {name} original\n".encode()
    patched = f"// {name} patched\n".encode()
    module = runtime / "dist" / module_name
    module.write_bytes(original if live == "source" else patched)
    module.chmod(0o644)
    state = root / name
    state.mkdir(mode=0o700, parents=True)
    source = hashlib.sha256(original).hexdigest()
    backup = state / f"{source}.js"
    backup.write_bytes(original)
    backup.chmod(0o600)
    receipt = {"schemaVersion": 1, "version": repair_module.VERSION, "module": module_name,
               "sourceSha256": source, "patchedSha256": hashlib.sha256(patched).hexdigest(),
               "backup": backup.name, "desiredSha256": hashlib.sha256(patched).hexdigest()}
    (state / "receipt.json").write_text(json.dumps(receipt, sort_keys=True))
    (state / "receipt.json").chmod(0o600)
    return module, original, patched, state


@pytest.fixture
def patch_root(installation, tmp_path):
    runtime, _, manifest, *_ = installation
    root = tmp_path / "ods-runtime-patches"
    repair_module.repair(runtime, root / "tool-recovery", manifest_path=manifest)
    return root


def snapshot(directory):
    return {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize("live", ["patched", "source"])
def test_foreign_patch_set_is_restored_and_archived(installation, patch_root, live):
    runtime, _, _, known_module, _, known_patched = installation
    module, original, _, state = write_foreign_set(runtime, patch_root, "file-operations", live=live)
    known_state = snapshot(patch_root / "tool-recovery")
    outcome = repair_module.restore_foreign(runtime, patch_root, {"tool-recovery"})
    assert module.read_bytes() == original
    assert module.stat().st_mode & 0o777 == 0o644
    assert known_module.read_bytes() == known_patched
    assert snapshot(patch_root / "tool-recovery") == known_state
    assert not state.exists()
    [entry] = outcome["foreign"]
    assert outcome["status"] == "changed"
    assert entry["status"] == ("changed" if live == "patched" else "unchanged")
    archived = Path(entry["archive"])
    assert archived.parent.parent == patch_root.with_name("ods-runtime-patches.retired")
    assert archived.parent.stat().st_mode & 0o777 == 0o700
    assert (archived / "receipt.json").exists()
    assert (archived / f"{hashlib.sha256(original).hexdigest()}.js").read_bytes() == original
    assert repair_module.restore_foreign(runtime, patch_root, {"tool-recovery"}) == {
        "status": "unchanged", "foreign": []}


def test_group_writable_state_root_from_owner_umask_is_tightened(installation, patch_root):
    # A user-private-group umask (002) left roots created by earlier builds
    # group-writable; that is the owner's own state, not a foreign directory.
    runtime = installation[0]
    patch_root.chmod(0o775)
    module, original, _, _ = write_foreign_set(runtime, patch_root, "file-operations")
    assert repair_module.restore_foreign(runtime, patch_root, {"tool-recovery"})["status"] == "changed"
    assert module.read_bytes() == original
    assert patch_root.stat().st_mode & 0o777 == 0o755


def test_repair_creates_owner_only_state_root_under_group_umask(installation, tmp_path):
    runtime, _, manifest, *_ = installation
    root = tmp_path / "fresh-patches"
    previous = os.umask(0o002)
    try:
        repair_module.repair(runtime, root / "tool-recovery", manifest_path=manifest)
    finally:
        os.umask(previous)
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "tool-recovery").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("damage", [
    "tampered-backup", "missing-backup", "unexpected-live", "other-version", "shared-module",
    "public-state"])
def test_foreign_patch_verification_failure_writes_nothing(installation, patch_root, damage):
    runtime = installation[0]
    valid_module, _, valid_patched, _ = write_foreign_set(
        runtime, patch_root, "a-valid", module_name="sandbox-Y3MbG9Od.js")
    module, original, patched, state = write_foreign_set(runtime, patch_root, "file-operations")
    source = hashlib.sha256(original).hexdigest()
    if damage == "tampered-backup":
        (state / f"{source}.js").write_bytes(b"tampered")
    elif damage == "missing-backup":
        (state / f"{source}.js").unlink()
    elif damage == "unexpected-live":
        patched = b"// independently changed\n"
        module.write_bytes(patched)
    elif damage == "other-version":
        receipt = json.loads((state / "receipt.json").read_text())
        receipt["version"] = "2026.7.1"
        (state / "receipt.json").write_text(json.dumps(receipt))
    elif damage == "shared-module":
        shutil.copytree(patch_root / "a-valid", patch_root / "z-duplicate")
    else:
        state.chmod(0o755)
    before = snapshot(patch_root.parent)
    with pytest.raises(ValueError, match="cannot be restored"):
        repair_module.restore_foreign(runtime, patch_root, {"tool-recovery"})
    assert snapshot(patch_root.parent) == before
    assert module.read_bytes() == patched
    assert valid_module.read_bytes() == valid_patched
    assert not patch_root.with_name("ods-runtime-patches.retired").exists()


def test_foreign_set_without_receipt_and_other_versions_are_left_alone(installation, patch_root):
    runtime = installation[0]
    (patch_root / "interrupted").mkdir(mode=0o700)
    before = snapshot(patch_root.parent)
    assert repair_module.restore_foreign(runtime, patch_root, {"tool-recovery"}) == {
        "status": "unchanged", "foreign": []}
    assert snapshot(patch_root.parent) == before
    module, _, patched, _ = write_foreign_set(runtime, patch_root, "file-operations")
    (runtime / "package.json").write_text(json.dumps({"name": "openclaw", "version": "future"}))
    before = snapshot(patch_root.parent)
    assert repair_module.restore_foreign(runtime, patch_root, {"tool-recovery"})["status"] == "not-applicable"
    assert snapshot(patch_root.parent) == before
    assert module.read_bytes() == patched


def test_foreign_restore_cli_used_by_the_installer(installation, patch_root):
    runtime = installation[0]
    module, original, _, _ = write_foreign_set(runtime, patch_root, "file-operations")
    (runtime / "openclaw.mjs").write_text("")
    command = [sys.executable, str(ROOT / "host/openclaw_tool_recovery.py"),
               "--openclaw-bin", str(runtime / "openclaw.mjs"),
               "--restore-foreign", str(patch_root), "--known", "tool-recovery"]
    rejected = subprocess.run(command[:-2], capture_output=True, text=True)
    assert rejected.returncode == 2 and "--known" in rejected.stderr
    assert module.read_bytes() != original
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(completed.stdout)["foreign"][0]["set"] == "file-operations"
    assert module.read_bytes() == original


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


@pytest.mark.parametrize('environment,manifest_name,module_name', [
    ('OPENCLAW_TOOL_SEARCH_MODULE', 'openclaw-image-envelope.json', repair_module.IMAGE_MODULE),
    ('OPENCLAW_SELECTION_MODULE', 'openclaw-compaction-budget.json', repair_module.COMPACTION_BUDGET_MODULE),
    ('OPENCLAW_READ_MODULE', 'openclaw-read-range.json', repair_module.READ_RANGE_MODULE),
    ('OPENCLAW_COMPACTION_RESUME_MODULE', 'openclaw-compaction-resume.json', repair_module.COMPACTION_RESUME_MODULE),
])
def test_reviewed_runtime_migrations_round_trip(tmp_path, environment, manifest_name, module_name):
    candidate_path = os.environ.get(environment)
    if not candidate_path:
        pytest.skip("requires the exact reviewed OpenClaw candidate")
    manifest_path = ROOT / 'host' / manifest_name
    manifest = json.loads(manifest_path.read_text())
    candidate = Path(candidate_path).read_bytes()
    predecessor = manifest.get('previousReplacements', {}).get(hashlib.sha256(candidate).hexdigest())
    if predecessor is not None:
        source = candidate.decode()
        for old, new in reversed(predecessor):
            assert source.count(new) == 1
            source = source.replace(new, old)
        candidate = source.encode()
        assert hashlib.sha256(candidate).hexdigest() == manifest['sourceSha256']
    if hashlib.sha256(candidate).hexdigest() == manifest['sourceSha256']:
        source = candidate.decode()
        for old, new in manifest['replacements']:
            assert source.count(old) == 1
            source = source.replace(old, new)
        candidate = source.encode()
    assert hashlib.sha256(candidate).hexdigest() == manifest["patchedSha256"]
    original = candidate.decode()
    for old, new in reversed(manifest["replacements"]):
        assert original.count(new) == 1
        original = original.replace(new, old)
    assert hashlib.sha256(original.encode()).hexdigest() == manifest["sourceSha256"]
    versions = {manifest["sourceSha256"]: [], **manifest["previousReplacements"],
                manifest["patchedSha256"]: manifest["replacements"]}
    for expected, replacements in versions.items():
        runtime = tmp_path / expected
        (runtime / "dist").mkdir(parents=True)
        (runtime / "package.json").write_text(json.dumps({"name": "openclaw", "version": repair_module.VERSION}))
        module = runtime / "dist" / module_name
        old_source = original
        for old, new in replacements:
            assert old_source.count(old) == 1
            old_source = old_source.replace(old, new)
        assert hashlib.sha256(old_source.encode()).hexdigest() == expected
        module.write_text(old_source)
        options = {"module_name": module_name, "manifest_path": manifest_path}
        repair_module.repair(runtime, runtime / "state", **options)
        assert module.read_bytes() == candidate
        assert repair_module.repair(runtime, runtime / "state", **options)["status"] == "unchanged"
        repair_module.repair(runtime, runtime / "state", restore=True, **options)
        assert module.read_bytes() == original.encode()
