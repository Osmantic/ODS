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
                                         repair_module.COMPACTION_IDLE_MODULE, repair_module.COMPACTION_BUDGET_MODULE,
                                         repair_module.TOOL_RESULT_PROJECTION_MODULE,
                                         repair_module.DIAGNOSTIC_STREAM_MODULE,
                                         repair_module.UTF8_OUTPUT_MODULE,
                                         repair_module.NO_TOOLS_INGRESS_MODULE,
                                         *repair_module.SANDBOX_MKDIR_MODULES.values()])
def test_additional_module_has_separate_exact_byte_custody(installation, module_name):
    runtime, state, manifest, module, original, patched = installation
    completion = module.with_name(module_name)
    module.rename(completion)
    dependency_sets = {repair_module.IMAGE_MODULE: repair_module.IMAGE_DEPENDENCIES,
                       repair_module.COMPACTION_BUDGET_MODULE: repair_module.COMPACTION_BUDGET_DEPENDENCIES}
    if module_name in dependency_sets:
        value = json.loads(manifest.read_text())
        value['reviewedDependencies'] = {}
        for name in dependency_sets[module_name]:
            dependency = runtime / 'dist' / name
            dependency.write_bytes(('reviewed native dependency ' + name).encode())
            value['reviewedDependencies'][name] = hashlib.sha256(dependency.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(value))
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


@pytest.mark.parametrize('dependency_name', sorted(repair_module.IMAGE_DEPENDENCIES))
def test_deferred_validation_helper_is_bound_before_repair_and_rechecked(installation, dependency_name):
    runtime, state, manifest, module, original, patched = installation
    module = module.rename(module.with_name(repair_module.IMAGE_MODULE))
    dependency = runtime / 'dist' / dependency_name
    reviewed = b'reviewed native validation'
    dependency.write_bytes(reviewed)
    value = json.loads(manifest.read_text())
    value['reviewedDependencies'] = {}
    for name in repair_module.IMAGE_DEPENDENCIES:
        raw = reviewed if name == dependency.name else ('reviewed native dependency ' + name).encode()
        (runtime / 'dist' / name).write_bytes(raw)
        value['reviewedDependencies'][name] = hashlib.sha256(raw).hexdigest()
    manifest.write_text(json.dumps(value))
    dependency.write_bytes(b'changed validation')
    with pytest.raises(ValueError, match='dependency differs'):
        run(installation, module_name=repair_module.IMAGE_MODULE)
    assert module.read_bytes() == original and not (state / 'receipt.json').exists()
    dependency.write_bytes(reviewed)
    assert run(installation, module_name=repair_module.IMAGE_MODULE)['status'] == 'changed'
    assert module.read_bytes() == patched
    dependency.write_bytes(b'changed after activation')
    with pytest.raises(ValueError, match='dependency differs'):
        run(installation, module_name=repair_module.IMAGE_MODULE)
    assert module.read_bytes() == patched


@pytest.fixture
def direct_installation(installation):
    runtime, _, manifest, module, *_ = installation
    module.rename(module.with_name(repair_module.DIRECT_TOOL_MODULE))
    value = json.loads(manifest.read_text())
    value['reviewedDependencies'] = {}
    for name in repair_module.DIRECT_TOOL_DEPENDENCIES:
        raw = ('reviewed dependency ' + name).encode()
        (runtime / 'dist' / name).write_bytes(raw)
        value['reviewedDependencies'][name] = hashlib.sha256(raw).hexdigest()
    manifest.write_text(json.dumps(value))
    return installation


def test_direct_completion_repairs_reapply_restore_and_recheck_dependencies(direct_installation):
    runtime, state, _, module, original, patched = direct_installation
    module = module.with_name(repair_module.DIRECT_TOOL_MODULE)
    assert run(direct_installation, module_name=repair_module.DIRECT_TOOL_MODULE)['status'] == 'changed'
    assert module.read_bytes() == patched
    assert run(direct_installation, module_name=repair_module.DIRECT_TOOL_MODULE)['status'] == 'unchanged'
    dependency = runtime / 'dist' / sorted(repair_module.DIRECT_TOOL_DEPENDENCIES)[0]
    previous = dependency.read_bytes()
    dependency.write_bytes(b'unreviewed implementation')
    with pytest.raises(ValueError, match='dependency differs'):
        run(direct_installation, module_name=repair_module.DIRECT_TOOL_MODULE)
    assert module.read_bytes() == patched
    dependency.write_bytes(previous)
    assert run(direct_installation, module_name=repair_module.DIRECT_TOOL_MODULE, restore=True)['status'] == 'changed'
    assert module.read_bytes() == original and (state / 'receipt.json').exists()


@pytest.mark.parametrize('fault', ['missing', 'changed', 'extra-contract'])
def test_direct_completion_unreviewed_dependency_never_publishes(direct_installation, fault):
    runtime, state, manifest, module, original, _ = direct_installation
    dependency = runtime / 'dist' / sorted(repair_module.DIRECT_TOOL_DEPENDENCIES)[0]
    if fault == 'missing':
        dependency.unlink()
    elif fault == 'changed':
        dependency.write_bytes(b'unreviewed implementation')
    else:
        value = json.loads(manifest.read_text())
        value['reviewedDependencies']['other.js'] = 'a' * 64
        manifest.write_text(json.dumps(value))
    with pytest.raises((ValueError, FileNotFoundError)):
        run(direct_installation, module_name=repair_module.DIRECT_TOOL_MODULE)
    assert module.with_name(repair_module.DIRECT_TOOL_MODULE).read_bytes() == original
    assert not (state / 'receipt.json').exists()


@pytest.fixture
def finalize_scope_installation(installation):
    runtime, _, manifest, module, *_ = installation
    module.rename(module.with_name(repair_module.FINALIZE_TOOL_SCOPE_MODULE))
    value = json.loads(manifest.read_text())
    value['reviewedDependencies'] = {}
    for name in repair_module.FINALIZE_TOOL_SCOPE_DEPENDENCIES:
        raw = ('reviewed native finalizer merge ' + name).encode()
        (runtime / 'dist' / name).write_bytes(raw)
        value['reviewedDependencies'][name] = hashlib.sha256(raw).hexdigest()
    manifest.write_text(json.dumps(value))
    return installation


def test_finalize_scope_has_separate_custody_and_binds_native_candidate_merge(finalize_scope_installation):
    runtime, state, _, module, original, patched = finalize_scope_installation
    module = module.with_name(repair_module.FINALIZE_TOOL_SCOPE_MODULE)
    options = {'module_name': repair_module.FINALIZE_TOOL_SCOPE_MODULE}
    assert run(finalize_scope_installation, **options)['status'] == 'changed'
    assert module.read_bytes() == patched
    receipt = json.loads((state / 'receipt.json').read_bytes())
    assert set(receipt['reviewedDependencies']) == repair_module.FINALIZE_TOOL_SCOPE_DEPENDENCIES
    assert next(state.glob('*.js')).read_bytes() == original
    assert run(finalize_scope_installation, **options)['status'] == 'unchanged'
    dependency = runtime / 'dist' / next(iter(repair_module.FINALIZE_TOOL_SCOPE_DEPENDENCIES))
    reviewed = dependency.read_bytes()
    dependency.write_bytes(b'unreviewed candidate merge')
    with pytest.raises(ValueError, match='dependency differs'):
        run(finalize_scope_installation, restore=True, **options)
    assert module.read_bytes() == patched
    dependency.write_bytes(reviewed)
    assert run(finalize_scope_installation, restore=True, **options)['status'] == 'changed'
    assert module.read_bytes() == original


@pytest.mark.parametrize('fault', ['missing', 'changed', 'extra-contract'])
def test_finalize_scope_unknown_merge_never_publishes(finalize_scope_installation, fault):
    runtime, state, manifest, module, original, _ = finalize_scope_installation
    dependency = runtime / 'dist' / next(iter(repair_module.FINALIZE_TOOL_SCOPE_DEPENDENCIES))
    if fault == 'missing':
        dependency.unlink()
    elif fault == 'changed':
        dependency.write_bytes(b'unreviewed native candidate merge')
    else:
        value = json.loads(manifest.read_text())
        value['reviewedDependencies']['other.js'] = 'a' * 64
        manifest.write_text(json.dumps(value))
    with pytest.raises((ValueError, FileNotFoundError)):
        run(finalize_scope_installation, module_name=repair_module.FINALIZE_TOOL_SCOPE_MODULE)
    assert module.with_name(repair_module.FINALIZE_TOOL_SCOPE_MODULE).read_bytes() == original
    assert not (state / 'receipt.json').exists()


def test_finalize_scope_installer_cli_selects_only_its_custodied_module(finalize_scope_installation, tmp_path):
    runtime, state, manifest, module, original, patched = finalize_scope_installation
    module = module.with_name(repair_module.FINALIZE_TOOL_SCOPE_MODULE)
    (runtime / 'openclaw.mjs').write_text('// fixture entrypoint; never executed\n')
    helper = tmp_path / 'cli' / 'openclaw_tool_recovery.py'
    helper.parent.mkdir()
    shutil.copyfile(ROOT / 'host/openclaw_tool_recovery.py', helper)
    shutil.copyfile(manifest, helper.with_name('openclaw-finalize-tool-scope.json'))
    command = [sys.executable, str(helper), '--openclaw-bin', str(runtime / 'openclaw.mjs'),
               '--finalize-tool-scope', '--state-dir', str(state)]
    for extra, expected, status in [([], patched, 'changed'), ([], patched, 'unchanged'),
                                    (['--restore'], original, 'changed')]:
        completed = subprocess.run(command + extra, capture_output=True, text=True, timeout=20)
        assert completed.returncode == 0, completed.stderr
        receipt = json.loads(completed.stdout)
        assert receipt['module'] == repair_module.FINALIZE_TOOL_SCOPE_MODULE
        assert receipt['status'] == status
        assert module.read_bytes() == expected
    assert not module.with_name(repair_module.YIELD_USAGE_MODULE).exists()
    assert not module.with_name(repair_module.COMPACTION_BUDGET_MODULE).exists()


@pytest.mark.parametrize('fault', ['missing', 'changed', 'extra-contract', 'changed-after-custody'])
def test_selection_scope_unknown_definition_lineage_never_publishes(installation, fault):
    runtime, state, manifest, module, original, patched = installation
    module = module.rename(module.with_name(repair_module.COMPACTION_BUDGET_MODULE))
    value = json.loads(manifest.read_text())
    value['reviewedDependencies'] = {}
    for name in repair_module.COMPACTION_BUDGET_DEPENDENCIES:
        raw = ('reviewed native definition lineage ' + name).encode()
        (runtime / 'dist' / name).write_bytes(raw)
        value['reviewedDependencies'][name] = hashlib.sha256(raw).hexdigest()
    manifest.write_text(json.dumps(value))
    dependency = runtime / 'dist' / sorted(repair_module.COMPACTION_BUDGET_DEPENDENCIES)[0]
    expected, receipt_before = original, None
    if fault == 'changed-after-custody':
        assert run(installation, module_name=repair_module.COMPACTION_BUDGET_MODULE)['status'] == 'changed'
        expected = patched
        receipt_before = (state / 'receipt.json').read_bytes()
    if fault == 'missing':
        dependency.unlink()
    elif fault in ('changed', 'changed-after-custody'):
        dependency.write_bytes(b'unreviewed native definition adapter')
    else:
        value['reviewedDependencies']['other.js'] = 'a' * 64
        manifest.write_text(json.dumps(value))
    with pytest.raises((ValueError, FileNotFoundError)):
        run(installation, module_name=repair_module.COMPACTION_BUDGET_MODULE)
    assert module.read_bytes() == expected
    if receipt_before is None:
        assert not (state / 'receipt.json').exists()
    else:
        assert (state / 'receipt.json').read_bytes() == receipt_before
        assert next(state.glob('*.js')).read_bytes() == original


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
    ('OPENCLAW_PLUGIN_TOOLS_MODULE', 'openclaw-plugin-tool-version.json', repair_module.PLUGIN_TOOL_VERSION_MODULE),
    ('OPENCLAW_TOOL_SEARCH_MODULE', 'openclaw-image-envelope.json', repair_module.IMAGE_MODULE),
    ('OPENCLAW_SELECTION_MODULE', 'openclaw-compaction-budget.json', repair_module.COMPACTION_BUDGET_MODULE),
    ('OPENCLAW_SUBAGENT_ADMISSION_MODULE', 'openclaw-subagent-admission.json', repair_module.SUBAGENT_ADMISSION_MODULE),
    ('OPENCLAW_AGENT_GATEWAY_MODULE', 'openclaw-subagent-session.json', repair_module.SUBAGENT_SESSION_MODULE),
    ('OPENCLAW_HOOK_CONTEXT_MODULE', 'openclaw-hook-provenance.json', repair_module.HOOK_PROVENANCE_MODULE),
    ('OPENCLAW_REDACT_MODULE', 'openclaw-run-id-redaction.json', repair_module.RUN_ID_REDACTION_MODULE),
    ('OPENCLAW_ATTEMPT_EXECUTION_MODULE', 'openclaw-context-usage.json', repair_module.CONTEXT_USAGE_MODULE),
    ('OPENCLAW_OPENAI_HTTP_MODULE', 'openclaw-no-tools-ingress.json', repair_module.NO_TOOLS_INGRESS_MODULE),
    ('OPENCLAW_EMBEDDED_AGENT_MODULE', 'openclaw-yield-usage.json', repair_module.YIELD_USAGE_MODULE),
    ('OPENCLAW_FINALIZE_LIFECYCLE_MODULE', 'openclaw-finalize-tool-scope.json', repair_module.FINALIZE_TOOL_SCOPE_MODULE),
    ('OPENCLAW_CORE_PROXY_MODULE', 'openclaw-compaction-empty.json', repair_module.COMPACTION_EMPTY_MODULE),
    ('OPENCLAW_COMPACT_MODULE', 'openclaw-compaction-no-work.json', repair_module.COMPACTION_NO_WORK_MODULE),
    ('OPENCLAW_READ_MODULE', 'openclaw-read-range.json', repair_module.READ_RANGE_MODULE),
    ('OPENCLAW_TRUNCATION_MODULE', 'openclaw-tool-result-projection.json', repair_module.TOOL_RESULT_PROJECTION_MODULE),
    ('OPENCLAW_COMPACTION_RESUME_MODULE', 'openclaw-compaction-resume.json', repair_module.COMPACTION_RESUME_MODULE),
    ('OPENCLAW_DIAGNOSTIC_EVENTS_MODULE', 'openclaw-diagnostic-stream-writes.json', repair_module.DIAGNOSTIC_STREAM_MODULE),
    ('OPENCLAW_PAYLOADS_MODULE', 'openclaw-command-attempt-warning.json', repair_module.COMMAND_ATTEMPT_MODULE),
    ('OPENCLAW_WINDOWS_COMMAND_MODULE', 'openclaw-utf8-output.json', repair_module.UTF8_OUTPUT_MODULE),
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
        for name, expected_sha in manifest.get('reviewedDependencies', {}).items():
            dependency = Path(candidate_path).with_name(name).read_bytes()
            assert hashlib.sha256(dependency).hexdigest() == expected_sha
            (runtime / 'dist' / name).write_bytes(dependency)
        options = {"module_name": module_name, "manifest_path": manifest_path}
        repair_module.repair(runtime, runtime / "state", **options)
        assert module.read_bytes() == candidate
        assert repair_module.repair(runtime, runtime / "state", **options)["status"] == "unchanged"
        repair_module.repair(runtime, runtime / "state", restore=True, **options)
        assert module.read_bytes() == original.encode()


@pytest.mark.parametrize('environment,manifest_name,module_name,prior_hash', [
    ('OPENCLAW_TOOL_SEARCH_MODULE', 'openclaw-image-envelope.json', repair_module.IMAGE_MODULE,
     'df9c6739312ed3c05ab33266f413c402f0f53dd8b15a5e9e430401221916a279'),
    ('OPENCLAW_SELECTION_MODULE', 'openclaw-compaction-budget.json', repair_module.COMPACTION_BUDGET_MODULE,
     '4eea7b3f5d3aab6d534f4b3916638ac3e9684a001a09c56fe80f153f79394227'),
    ('OPENCLAW_SELECTION_MODULE', 'openclaw-compaction-budget.json', repair_module.COMPACTION_BUDGET_MODULE,
     '07661b95137eceb301ad23bd4391c01ded07d3de0f7fb30741dde5a6c35df2e6'),
    ('OPENCLAW_EMBEDDED_AGENT_MODULE', 'openclaw-yield-usage.json', repair_module.YIELD_USAGE_MODULE,
     'd00804f960d617b6b3e2ae6b1691f3aa9866029c8da8772f236a35a6033b37f8'),
])
def test_composed_finalize_scope_recipes_keep_prior_build_custody(
        tmp_path, environment, manifest_name, module_name, prior_hash):
    candidate_path = os.environ.get(environment)
    if not candidate_path:
        pytest.skip('requires the exact reviewed OpenClaw candidate')
    manifest_path = ROOT / 'host' / manifest_name
    manifest = json.loads(manifest_path.read_text())
    candidate = Path(candidate_path).read_text()
    current_hash = hashlib.sha256(candidate.encode()).hexdigest()
    reverse = ([] if current_hash == manifest['sourceSha256'] else
               manifest['replacements'] if current_hash == manifest['patchedSha256'] else
               manifest['previousReplacements'][current_hash])
    for before, after in reversed(reverse):
        assert candidate.count(after) == 1
        candidate = candidate.replace(after, before)
    original = candidate.encode()
    assert hashlib.sha256(original).hexdigest() == manifest['sourceSha256']
    previous = dict(manifest, patchedSha256=prior_hash,
                    replacements=manifest['previousReplacements'][prior_hash])
    previous_path = tmp_path / 'previous.json'
    previous_path.write_text(json.dumps(previous))
    runtime = tmp_path / 'runtime'
    (runtime / 'dist').mkdir(parents=True)
    (runtime / 'package.json').write_text(json.dumps({'name': 'openclaw', 'version': repair_module.VERSION}))
    module = runtime / 'dist' / module_name
    module.write_bytes(original)
    for name, expected_sha in manifest.get('reviewedDependencies', {}).items():
        dependency = Path(candidate_path).with_name(name).read_bytes()
        assert hashlib.sha256(dependency).hexdigest() == expected_sha
        (runtime / 'dist' / name).write_bytes(dependency)
    state = tmp_path / 'state'

    def apply(path, **extra):
        return repair_module.repair(runtime, state, manifest_path=path, module_name=module_name, **extra)

    assert apply(previous_path)['status'] == 'changed'
    assert hashlib.sha256(module.read_bytes()).hexdigest() == prior_hash
    assert apply(manifest_path)['status'] == 'changed'
    assert hashlib.sha256(module.read_bytes()).hexdigest() == manifest['patchedSha256']
    # A previous ODS build can only rebuild the newer exact bytes from this
    # same private receipt and original backup; arbitrary bytes stay refused.
    assert apply(previous_path)['recoveredFrom'] == 'verified-backup'
    assert hashlib.sha256(module.read_bytes()).hexdigest() == prior_hash
    assert apply(manifest_path)['status'] == 'changed'
    assert apply(manifest_path, restore=True)['status'] == 'changed'
    assert module.read_bytes() == original
    assert list(state.glob('*.js')) == [state / (manifest['sourceSha256'] + '.js')]
    assert next(state.glob('*.js')).read_bytes() == original


@pytest.mark.skipif(not os.environ.get('ODS_TEST_OPENCLAW_RUNTIME'),
                    reason='explicit closed, read-only native SDK input required')
def test_scope_repair_prefixes_import_and_keep_ordinary_tools_after_admission_failures(tmp_path):
    """Load real SDK graphs and use a native read; never run a model/provider."""
    source = Path(os.environ['ODS_TEST_OPENCLAW_RUNTIME']).resolve(strict=True)
    runtime = tmp_path / 'runtime'
    shutil.copytree(source, runtime, symlinks=True)
    stages = [
        ('openclaw-finalize-tool-scope.json', repair_module.FINALIZE_TOOL_SCOPE_MODULE, None),
        ('openclaw-yield-usage.json', repair_module.YIELD_USAGE_MODULE,
         'd00804f960d617b6b3e2ae6b1691f3aa9866029c8da8772f236a35a6033b37f8'),
        ('openclaw-compaction-budget.json', repair_module.COMPACTION_BUDGET_MODULE,
         '07661b95137eceb301ad23bd4391c01ded07d3de0f7fb30741dde5a6c35df2e6'),
    ]
    for manifest_name, module_name, prior_hash in stages:
        manifest = json.loads((ROOT / 'host' / manifest_name).read_text())
        body = (runtime / 'dist' / module_name).read_text()
        assert hashlib.sha256(body.encode()).hexdigest() == manifest['patchedSha256']
        for before, after in reversed(manifest['replacements']):
            assert body.count(after) == 1
            body = body.replace(after, before)
        assert hashlib.sha256(body.encode()).hexdigest() == manifest['sourceSha256']
        for before, after in manifest['previousReplacements'].get(prior_hash, []):
            assert body.count(before) == 1
            body = body.replace(before, after)
        assert hashlib.sha256(body.encode()).hexdigest() == (prior_hash or manifest['sourceSha256'])
        (runtime / 'dist' / module_name).write_text(body)

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    marker = 'ODS ordinary uncapped native read fixture'
    (workspace / 'ordinary.txt').write_text(marker)
    state = tmp_path / 'isolated-sdk-state'
    state.mkdir()
    config = state / 'config.json'
    config.write_text('{}')
    environment = {**os.environ, 'OPENCLAW_STATE_DIR': str(state),
                   'OPENCLAW_CONFIG_PATH': str(config)}
    script = r'''
import { pathToFileURL } from 'node:url';
const root=process.argv[1], workspace=process.argv[2];
const native=await import(pathToFileURL(root+'/dist/plugin-sdk/agent-harness.js'));
await import(pathToFileURL(root+'/dist/embedded-agent-CJx-nG3W.js'));
await import(pathToFileURL(root+'/dist/selection-BEwSQKM-.js'));
const tools=native.createOpenClawCodingTools({agentId:'ordinary-fixture',
 sessionId:'ordinary-session',sessionKey:'agent:ordinary-fixture:main',runId:'ordinary-run',
 cwd:workspace,workspaceDir:workspace,
 config:{agents:{defaults:{workspace}},tools:{profile:'coding',fs:{workspaceOnly:true}},plugins:{enabled:false}},
 includeToolSearchControls:false,toolConstructionPlan:{includeBaseCodingTools:true,
 includeShellTools:false,includeOpenClawTools:false,includePluginTools:false,includeChannelTools:false}});
const allowed=native.applyEmbeddedAttemptToolsAllow(tools,undefined);
const read=allowed.find(tool=>tool.name==='read');
if(!read)throw Error('ordinary native read was removed');
const result=await read.execute('ordinary-read',{path:workspace+'/ordinary.txt'});
const text=(result.content??[]).filter(item=>item.type==='text').map(item=>item.text).join('\n');
if(!text.includes('ODS ordinary uncapped native read fixture'))throw Error('ordinary read did not execute');
console.log('ODS_PREFIX_ORDINARY='+JSON.stringify({loaded:true,read:true,names:allowed.map(tool=>tool.name)}));
'''

    def ordinary():
        completed = subprocess.run(['node', '--input-type=module', '-e', script, str(runtime), str(workspace)],
                                   capture_output=True, text=True, env=environment, timeout=45)
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout.split('ODS_PREFIX_ORDINARY=')[-1])
        assert result['loaded'] is True and result['read'] is True
        assert 'read' in result['names'] and not {'exec', 'message', 'tool_call'} & set(result['names'])

    def module_bytes():
        return {module: (runtime / 'dist' / module).read_bytes() for _, module, _ in stages}

    completed_states = []

    def custody_bytes():
        return {str(path): path.read_bytes() for directory in completed_states
                for path in directory.iterdir() if path.name != 'lock'}

    ordinary()
    for index, (manifest_name, module_name, _) in enumerate(stages):
        actual_manifest = ROOT / 'host' / manifest_name
        manifest = json.loads(actual_manifest.read_text())
        faults = ['source', 'custody'] + (['dependency'] if manifest.get('reviewedDependencies') else [])
        for fault in faults:
            before, custody_before = module_bytes(), custody_bytes()
            fault_state = tmp_path / f'fault-{index}-{fault}'
            fault_state.mkdir(mode=0o700)
            selected_manifest = actual_manifest
            dependency = saved_dependency = None
            if fault == 'source':
                selected_manifest = tmp_path / f'fault-{index}-manifest.json'
                selected_manifest.write_text(json.dumps({**manifest, 'sourceSha256': '0' * 64}))
            elif fault == 'custody':
                backup = fault_state / (manifest['sourceSha256'] + '.js')
                backup.write_bytes(b'tampered fixture custody')
                backup.chmod(0o600)
            else:
                dependency = runtime / 'dist' / sorted(manifest['reviewedDependencies'])[0]
                saved_dependency = dependency.read_bytes()
                # A loadable but unreviewed dependency must still be refused.
                dependency.write_bytes(saved_dependency + b'\n// fixture dependency drift\n')
            try:
                with pytest.raises(ValueError):
                    repair_module.repair(runtime, fault_state, module_name=module_name,
                                         manifest_path=selected_manifest)
                assert module_bytes() == before and custody_bytes() == custody_before
                assert not (fault_state / 'receipt.json').exists()
                ordinary()
            finally:
                if dependency is not None:
                    dependency.write_bytes(saved_dependency)
        selected_state = tmp_path / f'applied-{index}'
        assert repair_module.repair(runtime, selected_state, module_name=module_name,
                                    manifest_path=actual_manifest)['status'] == 'changed'
        completed_states.append(selected_state)
        assert hashlib.sha256((runtime / 'dist' / module_name).read_bytes()).hexdigest() == manifest['patchedSha256']
        ordinary()


def test_utf8_output_cli_refuses_unreviewed_decoder_bytes(installation):
    runtime = installation[0]
    module = runtime / "dist" / repair_module.UTF8_OUTPUT_MODULE
    changed = b"function createWindowsOutputDecoder() {}\n"
    module.write_bytes(changed)
    (runtime / "openclaw.mjs").write_text("")
    state = runtime.parent / "utf8-output"
    completed = subprocess.run([sys.executable, str(ROOT / "host/openclaw_tool_recovery.py"),
                               "--openclaw-bin", str(runtime / "openclaw.mjs"), "--utf8-output",
                               "--state-dir", str(state)], capture_output=True, text=True)
    assert completed.returncode != 0 and "differs from reviewed bytes" in completed.stderr
    assert module.read_bytes() == changed
    assert not (state / "receipt.json").exists()


def test_diagnostic_stream_writes_cli_binds_only_the_pinned_observer_module(installation):
    runtime = installation[0]
    manifest = json.loads((ROOT / "host/openclaw-diagnostic-stream-writes.json").read_text())
    [(pinned, repaired)] = manifest["replacements"]
    assert pinned.count("new Proxy(stream, ") == repaired.count("new Proxy(stream, ") == 1
    assert "set(target, property, value, receiver)" in repaired and "set(" not in pinned
    module = runtime / "dist" / repair_module.DIAGNOSTIC_STREAM_MODULE
    changed = b"function observeModelCallStream() {}\n"
    module.write_bytes(changed)
    (runtime / "openclaw.mjs").write_text("")
    completed = subprocess.run([sys.executable, str(ROOT / "host/openclaw_tool_recovery.py"),
                                "--openclaw-bin", str(runtime / "openclaw.mjs"), "--diagnostic-stream-writes",
                                "--state-dir", str(runtime.parent / "diagnostic-stream-writes")],
                               capture_output=True, text=True)
    assert completed.returncode != 0 and "differs from reviewed bytes" in completed.stderr
    assert module.read_bytes() == changed
