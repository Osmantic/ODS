from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import sys
from dataclasses import replace
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
CATALOG_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "extensions-catalog.json"
)
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_data_backup_runtime as data_runtime
from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedImage,
    PlannedOperation,
)
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkValidationError,
)

SUPPORTED = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and all(
        function in os.supports_dir_fd
        for function in (os.open, os.stat, os.unlink, os.mkdir, os.rmdir, os.link)
    )
    and os.stat in os.supports_follow_symlinks
    and os.link in os.supports_follow_symlinks
    and os.listdir in os.supports_fd
)
pytestmark = pytest.mark.skipif(
    not SUPPORTED, reason="requires Linux descriptor-relative filesystem semantics"
)

TXN = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
REQUEST_HASH = "3" * 64


def canonical_document(*, data=None) -> bytes:
    value = {"data": [data or data_runtime.CANARY_DATA_RECORD]}
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def definition(**changes) -> PlannedDefinition:
    values = {
        "service_id": data_runtime.CANARY_SERVICE_ID,
        "service_type": "docker",
        "manifest_schema_version": data_runtime.CANARY_MANIFEST_SCHEMA,
        "version": data_runtime.CANARY_VERSION,
        "data_schema_version": data_runtime.CANARY_DATA_SCHEMA_VERSION,
        "definition_sha256": data_runtime.CANARY_DEFINITION_SHA256,
        "compose_sha256": data_runtime.CANARY_COMPOSE_SHA256,
        "definition_source": "builtin",
        "compose_file": "compose.yaml",
        "images": (
            PlannedImage(
                reference=data_runtime.CANARY_IMAGE_REFERENCE,
                digest=data_runtime.CANARY_IMAGE_DIGEST,
                download_bytes=data_runtime.CANARY_IMAGE_DOWNLOAD_BYTES,
            ),
        ),
        "builds": (),
        "canonical_document": canonical_document(),
        "host_ports": (),
        "exclusive": (),
    }
    values.update(changes)
    return PlannedDefinition(**values)


def material(
    operation_key="backup",
    *,
    action="install",
    definition_value=None,
) -> LifecyclePlanMaterial:
    state = "configuring" if operation_key == "backup" else "reconciling"
    return LifecyclePlanMaterial(
        schema=PLAN_MATERIAL_SCHEMA,
        transaction_id=TXN,
        plan_hash=PLAN_HASH,
        state=state,
        operations=(PlannedOperation(data_runtime.CANARY_SERVICE_ID, action),),
        definitions=(definition_value or definition(),),
    )


def command(operation_key="backup", *, bound=True, action="install", **changes):
    values = {
        "transaction_id": TXN,
        "plan_hash": PLAN_HASH,
        "operation_key": operation_key,
        "request_hash": REQUEST_HASH,
        "service_ids": (data_runtime.CANARY_SERVICE_ID,),
        "payload": {"serviceIds": [data_runtime.CANARY_SERVICE_ID]},
        "timeout_seconds": 600,
        "plan_material": (
            material(operation_key, action=action) if bound else None
        ),
    }
    values.update(changes)
    return LifecycleWorkCommand(**values)


def loader(value: LifecycleWorkCommand) -> LifecycleWorkCommand:
    return replace(value, plan_material=material(value.operation_key))


@pytest.fixture()
def roots(tmp_path):
    install = tmp_path / "install"
    data = install / "data"
    config = install / "config"
    backup_root = data / "assistant-first" / "data-backups"
    install.mkdir(mode=0o700)
    data.mkdir(mode=0o700)
    config.mkdir(mode=0o700)
    backup_root.mkdir(mode=0o700, parents=True)
    install.chmod(0o700)
    data.chmod(0o700)
    config.chmod(0o700)
    backup_root.parent.chmod(0o700)
    backup_root.chmod(0o700)
    return install, data, config, backup_root


def build(roots):
    install, data, _config, _backup_root = roots
    return data_runtime.build_data_backup_runtime(
        install_dir=install,
        data_dir=data,
        plan_loader=loader,
    )


def write_tree(config: Path, *, content=b"original") -> Path:
    source = config / "searxng"
    nested = source / "nested"
    source.mkdir(mode=0o750)
    nested.mkdir(mode=0o700)
    source.chmod(0o750)
    nested.chmod(0o700)
    settings = source / "settings.yml"
    settings.write_bytes(content)
    settings.chmod(0o640)
    token = nested / "token"
    token.write_bytes(b"private")
    token.chmod(0o600)
    return source


def assert_original_tree(source: Path) -> None:
    assert source.is_dir()
    assert stat.S_IMODE(source.stat().st_mode) == 0o750
    assert stat.S_IMODE((source / "nested").stat().st_mode) == 0o700
    assert (source / "settings.yml").read_bytes() == b"original"
    assert stat.S_IMODE((source / "settings.yml").stat().st_mode) == 0o640
    assert (source / "nested" / "token").read_bytes() == b"private"
    assert stat.S_IMODE((source / "nested" / "token").stat().st_mode) == 0o600


def test_canary_data_allowlist_matches_generated_catalog():
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    entries = [
        item
        for item in catalog["extensions"]
        if item["id"] == data_runtime.CANARY_SERVICE_ID
    ]
    assert len(entries) == 1
    assert entries[0]["planning"]["data"] == [
        data_runtime.CANARY_DATA_RECORD
    ]


def test_present_tree_round_trips_exact_content_and_modes(roots):
    _install, _data, config, backup_root = roots
    source = write_tree(config)
    runtime = build(roots)

    backup_evidence = runtime.backup_dispatcher(command())
    snapshots = list(backup_root.iterdir())
    assert len(snapshots) == 1
    assert stat.S_IMODE(backup_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(snapshots[0].stat().st_mode) == 0o400
    assert snapshots[0].stat().st_nlink == 1
    assert len(backup_evidence) == 64
    snapshot = json.loads(snapshots[0].read_text(encoding="ascii"))
    settings = next(
        item
        for item in snapshot["data"]["state"]["entries"]
        if item["path"] == "settings.yml"
    )
    assert base64.b64decode(settings["content"], validate=True) == b"original"
    assert "original" not in backup_evidence

    (source / "settings.yml").write_bytes(b"changed")
    (source / "settings.yml").chmod(0o600)
    (source / "extra").write_bytes(b"new")
    (source / "extra").chmod(0o600)
    restore_evidence = runtime.restore_dispatcher(command("restore"))

    assert restore_evidence != backup_evidence
    assert_original_tree(source)
    assert not (source / "extra").exists()


def test_absent_snapshot_removes_only_exact_canary_tree(roots):
    _install, _data, config, _backup_root = roots
    runtime = build(roots)
    runtime.backup_dispatcher(command())

    sibling = config / "preserved"
    sibling.write_bytes(b"keep")
    sibling.chmod(0o600)
    source = write_tree(config, content=b"created-later")
    runtime.restore_dispatcher(command("restore"))

    assert not source.exists()
    assert sibling.read_bytes() == b"keep"
    assert config.is_dir()


def test_backup_replay_is_first_write_wins(roots):
    _install, _data, config, _backup_root = roots
    source = write_tree(config)
    runtime = build(roots)
    first = runtime.backup_dispatcher(command())

    (source / "settings.yml").write_bytes(b"later")
    (source / "settings.yml").chmod(0o600)
    second = runtime.backup_dispatcher(command())
    runtime.restore_dispatcher(command("restore"))

    assert second == first
    assert_original_tree(source)


def test_crash_after_link_is_stabilized_without_republishing(roots):
    _install, _data, config, backup_root = roots
    write_tree(config)
    runtime = build(roots)
    expected = runtime.backup_dispatcher(command())
    snapshot = next(backup_root.iterdir())
    crash_temp = backup_root / f".{snapshot.name}.{'a' * 32}.tmp"
    os.link(snapshot, crash_temp)
    assert snapshot.stat().st_nlink == 2

    observed = runtime.backup_started_observer(command(bound=False))

    assert observed.state == "completed"
    assert observed.evidence_hash == expected
    assert not crash_temp.exists()
    assert snapshot.stat().st_nlink == 1


def test_external_snapshot_hardlink_fails_closed_without_unlinking_it(roots):
    _install, _data, config, backup_root = roots
    write_tree(config)
    runtime = build(roots)
    runtime.backup_dispatcher(command())
    snapshot = next(backup_root.iterdir())
    external = backup_root.parent / "external-snapshot-link"
    os.link(snapshot, external)

    with pytest.raises(
        data_runtime.DataBackupRuntimeError,
        match="^lifecycle-work-data-backup-custody-invalid$",
    ):
        runtime.backup_started_observer(command(bound=False))

    assert external.exists()
    assert external.stat().st_ino == snapshot.stat().st_ino
    assert snapshot.stat().st_nlink == 2


def test_sealed_crash_before_link_orphan_is_removed_during_backup_recovery(roots):
    _install, _data, config, backup_root = roots
    write_tree(config)
    runtime = build(roots)
    backup_command = command()
    name = data_runtime._snapshot_name(backup_command)
    orphan = backup_root / f".{name}.{'b' * 32}.tmp"
    orphan.write_bytes(b"sealed orphan\n")
    orphan.chmod(0o400)

    evidence = runtime.backup_dispatcher(backup_command)

    assert len(evidence) == 64
    assert not orphan.exists()
    assert [path.name for path in backup_root.iterdir()] == [name]


def test_sealed_temp_is_not_removed_until_a_final_snapshot_exists(roots):
    _install, _data, _config, backup_root = roots
    name = data_runtime._snapshot_name(command())
    active = backup_root / f".{name}.{'d' * 32}.tmp"
    active.write_bytes(b"sealed active publisher\n")
    active.chmod(0o400)
    descriptor = os.open(backup_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        data_runtime._stabilize_snapshot(descriptor, name)
    finally:
        os.close(descriptor)

    assert active.exists()
    assert stat.S_IMODE(active.stat().st_mode) == 0o400


def test_publish_recovers_when_observer_already_unlinked_temp(roots, monkeypatch):
    _install, _data, config, backup_root = roots
    write_tree(config)
    runtime = build(roots)
    backup_command = command()
    name = data_runtime._snapshot_name(backup_command)
    original_unlink = os.unlink
    raced = False

    def observer_unlink(path, *args, **kwargs):
        nonlocal raced
        if (
            not raced
            and isinstance(path, str)
            and path.startswith(f".{name}.")
            and path.endswith(".tmp")
            and (backup_root / name).exists()
        ):
            raced = True
            original_unlink(path, *args, **kwargs)
            raise FileNotFoundError(path)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(data_runtime.os, "unlink", observer_unlink)
    evidence = runtime.backup_dispatcher(backup_command)

    assert raced is True
    assert len(evidence) == 64
    snapshot = backup_root / name
    assert snapshot.exists()
    assert snapshot.stat().st_nlink == 1


def test_losing_publish_recovers_when_observer_unlinks_temp_before_link(
    roots, monkeypatch
):
    _install, _data, config, backup_root = roots
    write_tree(config)
    runtime = build(roots)
    backup_command = command()
    runtime.backup_dispatcher(backup_command)
    name = data_runtime._snapshot_name(backup_command)
    snapshot = backup_root / name
    expected = snapshot.read_bytes()
    original_link = os.link
    raced = False

    def observer_unlink_before_link(source, target, *args, **kwargs):
        nonlocal raced
        if (
            not raced
            and source.startswith(f".{name}.")
            and source.endswith(".tmp")
        ):
            raced = True
            os.unlink(source, dir_fd=kwargs["src_dir_fd"])
            raise FileNotFoundError(source)
        return original_link(source, target, *args, **kwargs)

    monkeypatch.setattr(data_runtime.os, "link", observer_unlink_before_link)
    descriptor = os.open(backup_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        loaded, _document = data_runtime._publish_snapshot(
            descriptor,
            name,
            b"losing publisher payload\n",
            backup_command,
            data_runtime._validate_bound_command(backup_command, "backup"),
        )
    finally:
        os.close(descriptor)

    assert raced is True
    assert loaded == expected
    assert snapshot.stat().st_nlink == 1
    assert [path.name for path in backup_root.iterdir()] == [name]


def test_stabilization_tolerates_temp_unlink_race(roots, monkeypatch):
    _install, _data, config, backup_root = roots
    write_tree(config)
    runtime = build(roots)
    expected = runtime.backup_dispatcher(command())
    snapshot = next(backup_root.iterdir())
    crash_temp = backup_root / f".{snapshot.name}.{'c' * 32}.tmp"
    os.link(snapshot, crash_temp)
    original_unlink = os.unlink
    raced = False

    def racing_unlink(path, *args, **kwargs):
        nonlocal raced
        if not raced and path == crash_temp.name and kwargs.get("dir_fd") is not None:
            raced = True
            original_unlink(path, *args, **kwargs)
            raise FileNotFoundError(path)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(data_runtime.os, "unlink", racing_unlink)
    observed = runtime.backup_started_observer(command(bound=False))

    assert raced is True
    assert observed.state == "completed"
    assert observed.evidence_hash == expected
    assert snapshot.stat().st_nlink == 1


def test_started_observers_recover_completed_effects(roots):
    _install, _data, config, _backup_root = roots
    source = write_tree(config)
    runtime = build(roots)

    missing = runtime.backup_started_observer(command(bound=False))
    assert missing.state == "missing"
    expected_backup = runtime.backup_dispatcher(command())
    completed = runtime.backup_started_observer(command(bound=False))
    assert completed.state == "completed"
    assert completed.evidence_hash == expected_backup

    (source / "settings.yml").write_bytes(b"changed")
    (source / "settings.yml").chmod(0o600)
    assert runtime.restore_started_observer(
        command("restore", bound=False)
    ).state == "missing"
    expected_restore = runtime.restore_dispatcher(command("restore"))
    restored = runtime.restore_started_observer(command("restore", bound=False))
    assert restored.state == "completed"
    assert restored.evidence_hash == expected_restore


def test_tampered_snapshot_is_rejected_before_live_tree_mutation(roots):
    _install, _data, config, backup_root = roots
    source = write_tree(config)
    runtime = build(roots)
    runtime.backup_dispatcher(command())
    snapshot = next(backup_root.iterdir())
    document = json.loads(snapshot.read_text(encoding="ascii"))
    settings = next(
        item
        for item in document["data"]["state"]["entries"]
        if item["path"] == "settings.yml"
    )
    settings["content"] = base64.b64encode(b"tampered").decode("ascii")
    tampered = (
        json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")
    snapshot.chmod(0o600)
    snapshot.write_bytes(tampered)
    snapshot.chmod(0o400)
    before = (source / "settings.yml").read_bytes()

    with pytest.raises(
        data_runtime.DataBackupRuntimeError,
        match="^lifecycle-work-data-backup-invalid$",
    ):
        runtime.restore_dispatcher(command("restore"))
    assert (source / "settings.yml").read_bytes() == before


def test_source_symlink_is_rejected_without_snapshot(roots, tmp_path):
    _install, _data, config, backup_root = roots
    target = tmp_path / "outside"
    target.mkdir()
    (config / "searxng").symlink_to(target, target_is_directory=True)

    with pytest.raises(
        data_runtime.DataBackupRuntimeError,
        match="^lifecycle-work-data-path-unavailable$",
    ):
        build(roots).backup_dispatcher(command())
    assert list(backup_root.iterdir()) == []


def test_restore_rejects_symlink_target_before_touching_outside(roots, tmp_path):
    _install, _data, config, _backup_root = roots
    source = write_tree(config)
    runtime = build(roots)
    runtime.backup_dispatcher(command())
    shutil.rmtree(source)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker"
    marker.write_bytes(b"untouched")
    (config / "searxng").symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        data_runtime.DataBackupRuntimeError,
        match="^lifecycle-work-data-path-unavailable$",
    ):
        runtime.restore_dispatcher(command("restore"))
    assert marker.read_bytes() == b"untouched"


def test_hardlink_and_special_files_are_rejected(roots):
    _install, _data, config, backup_root = roots
    source = config / "searxng"
    source.mkdir(mode=0o700)
    original = source / "original"
    original.write_bytes(b"same inode")
    original.chmod(0o600)
    os.link(original, source / "alias")

    with pytest.raises(
        data_runtime.DataBackupRuntimeError,
        match="^lifecycle-work-data-custody-invalid$",
    ):
        build(roots).backup_dispatcher(command())
    assert list(backup_root.iterdir()) == []

    (source / "alias").unlink()
    original.unlink()
    os.mkfifo(source / "pipe", mode=0o600)
    with pytest.raises(
        data_runtime.DataBackupRuntimeError,
        match="^lifecycle-work-data-special-file-denied$",
    ):
        build(roots).backup_dispatcher(command())
    assert list(backup_root.iterdir()) == []


@pytest.mark.parametrize(
    "changed",
    [
        {"operation_key": "configure"},
        {"service_ids": ("documents",)},
        {"payload": {"serviceIds": ["documents"]}},
        {"plan_material": material("backup", definition_value=definition(service_id="other"))},
        {
            "plan_material": material(
                "backup",
                definition_value=definition(
                    canonical_document=canonical_document(
                        data={**data_runtime.CANARY_DATA_RECORD, "path": "config/other"}
                    )
                ),
            )
        },
        {"plan_material": material("restore")},
    ],
)
def test_misbound_commands_fail_before_snapshot(roots, changed):
    _install, _data, config, backup_root = roots
    write_tree(config)
    with pytest.raises(LifecycleWorkValidationError):
        build(roots).backup_dispatcher(command(**changed))
    assert list(backup_root.iterdir()) == []


def test_entry_limit_fails_before_snapshot(roots, monkeypatch):
    _install, _data, config, backup_root = roots
    source = config / "searxng"
    source.mkdir(mode=0o700)
    for name in ("one", "two"):
        child = source / name
        child.write_bytes(name.encode("ascii"))
        child.chmod(0o600)
    monkeypatch.setattr(data_runtime, "_MAX_ENTRIES", 1)

    with pytest.raises(
        data_runtime.DataBackupRuntimeError,
        match="^lifecycle-work-data-entry-limit$",
    ):
        build(roots).backup_dispatcher(command())
    assert list(backup_root.iterdir()) == []


def test_missing_restore_snapshot_fails_before_live_tree_mutation(roots):
    _install, _data, config, _backup_root = roots
    source = write_tree(config)
    before = (source / "settings.yml").read_bytes()

    with pytest.raises(
        data_runtime.DataBackupRuntimeError,
        match="^lifecycle-work-data-backup-missing$",
    ):
        build(roots).restore_dispatcher(command("restore"))

    assert (source / "settings.yml").read_bytes() == before
    assert_original_tree(source)


def test_restore_replay_is_an_exact_noop(roots):
    _install, _data, config, _backup_root = roots
    source = write_tree(config)
    runtime = build(roots)
    runtime.backup_dispatcher(command())
    (source / "settings.yml").write_bytes(b"changed")
    (source / "settings.yml").chmod(0o600)
    first = runtime.restore_dispatcher(command("restore"))
    settings = source / "settings.yml"
    identity = (settings.stat().st_ino, settings.stat().st_mtime_ns, settings.stat().st_mode)

    second = runtime.restore_dispatcher(command("restore"))

    assert second == first
    assert (settings.stat().st_ino, settings.stat().st_mtime_ns, settings.stat().st_mode) == identity
    assert_original_tree(source)


def test_depth_limit_fails_before_snapshot(roots, monkeypatch):
    _install, _data, config, backup_root = roots
    source = config / "searxng"
    nested = source / "one" / "two"
    nested.mkdir(mode=0o700, parents=True)
    source.chmod(0o700)
    (source / "one").chmod(0o700)
    nested.chmod(0o700)
    monkeypatch.setattr(data_runtime, "_MAX_DEPTH", 1)

    with pytest.raises(
        data_runtime.DataBackupRuntimeError,
        match="^lifecycle-work-data-depth-limit$",
    ):
        build(roots).backup_dispatcher(command())
    assert list(backup_root.iterdir()) == []


@pytest.mark.parametrize(
    ("maximum_file_bytes", "maximum_total_bytes", "files"),
    [
        (3, 8, {"large": b"1234"}),
        (4, 5, {"one": b"123", "two": b"456"}),
    ],
)
def test_size_limits_fail_before_snapshot(
    roots, monkeypatch, maximum_file_bytes, maximum_total_bytes, files
):
    _install, _data, config, backup_root = roots
    source = config / "searxng"
    source.mkdir(mode=0o700)
    source.chmod(0o700)
    for name, content in files.items():
        child = source / name
        child.write_bytes(content)
        child.chmod(0o600)
    monkeypatch.setattr(data_runtime, "_MAX_FILE_BYTES", maximum_file_bytes)
    monkeypatch.setattr(data_runtime, "_MAX_TOTAL_BYTES", maximum_total_bytes)

    with pytest.raises(
        data_runtime.DataBackupRuntimeError,
        match="^lifecycle-work-data-size-limit$",
    ):
        build(roots).backup_dispatcher(command())
    assert list(backup_root.iterdir()) == []


def test_serialized_snapshot_size_limit_fails_before_publication(roots, monkeypatch):
    _install, _data, config, backup_root = roots
    write_tree(config)
    monkeypatch.setattr(data_runtime, "_MAX_SNAPSHOT_BYTES", 1)

    with pytest.raises(
        data_runtime.DataBackupRuntimeError,
        match="^lifecycle-work-data-size-limit$",
    ):
        build(roots).backup_dispatcher(command())

    assert list(backup_root.iterdir()) == []


def test_invalid_backup_root_mode_fails_construction_without_repair(roots):
    _install, _data, _config, backup_root = roots
    backup_root.chmod(0o750)

    with pytest.raises(
        data_runtime.DataBackupRuntimeError,
        match="^lifecycle-work-data-custody-invalid$",
    ):
        build(roots)

    assert stat.S_IMODE(backup_root.stat().st_mode) == 0o750
    assert list(backup_root.iterdir()) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"transaction_id": "../escape"},
        {"plan_hash": "not-a-hash"},
        {"request_hash": None},
    ],
)
def test_invalid_snapshot_identifiers_fail_before_filesystem_effects(roots, changes):
    _install, _data, config, backup_root = roots
    write_tree(config)

    with pytest.raises(
        LifecycleWorkValidationError,
        match="^lifecycle-work-command-invalid$",
    ):
        build(roots).backup_dispatcher(command(**changes))

    assert list(backup_root.iterdir()) == []


def test_construction_is_inert(roots):
    _install, _data, config, backup_root = roots
    source = write_tree(config)
    runtime = build(roots)

    assert isinstance(runtime.store, data_runtime.DataBackupStore)
    assert list(backup_root.iterdir()) == []
    assert_original_tree(source)
