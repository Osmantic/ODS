"""Source-only, one-path paired transition for verified extension data.

The caller must stage a verified stream snapshot and supply a host-owned
quiescence check. This module is not selected by ods-host-agent. It never
overwrites, unlinks, or automatically rolls back live data; the original tree
is retained under the intent's deterministic quarantine name. Same-UID
adversaries and hidden filesystem metadata still require live qualification.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Callable

from extension_data_backup_runtime import (
    DataBackupRuntimeError,
    _close_quietly,
    _directory_flags,
    _file_read_flags,
    _identity,
    _open_absolute_directory,
    _open_relative_directory,
    _safe_relative_parts,
    _validate_name,
    _validate_platform,
    _validate_source_mode,
)
from extension_data_restore_journal import (
    RestoreIntentJournal,
    RestoreJournalError,
    _canonical,
    _publish,
    _read,
)
from extension_data_stream_restore import (
    StreamRestoreStageError,
    _plan,
    _verify_tree,
)
from extension_data_stream_snapshot import (
    StreamSnapshotError,
    StreamSnapshotStore,
    _check_extended_metadata,
    _check_sparse_file,
    _MAX_DEPTH,
    _MAX_ENTRIES,
    _MAX_FILE_BYTES,
    _MAX_TOTAL_BYTES,
)
from extension_lifecycle_work import LifecycleWorkCommand, LifecycleWorkExecutionError


TRANSITION_SCHEMA = "ods.extension-data-paired-transition.v1"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_RENAME_NOREPLACE = 1


class PairedTransitionError(LifecycleWorkExecutionError):
    """Value-free refusal of an unsafe or ambiguous live transition."""


def _fail(code: str, cause: BaseException | None = None) -> None:
    if cause is None:
        raise PairedTransitionError(code) from None
    raise PairedTransitionError(code) from cause


def _require_quiesced(check: Callable[[], bool] | None) -> None:
    if not callable(check):
        _fail("lifecycle-work-data-transition-quiescence-required")
    try:
        if check() is not True:
            _fail("lifecycle-work-data-transition-not-quiesced")
    except PairedTransitionError:
        raise
    except BaseException as exc:
        _fail("lifecycle-work-data-transition-quiescence-unavailable", exc)


def _no_replace(parent: int, source: str, destination: str) -> None:
    """Refuse, rather than silently fall back, when Linux no-clobber is absent."""
    try:
        function = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as exc:
        _fail("lifecycle-work-data-transition-no-replace-unsupported", exc)
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                         ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(parent, os.fsencode(source), parent, os.fsencode(destination),
                _RENAME_NOREPLACE) != 0:
        error = ctypes.get_errno()
        if error in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
            _fail("lifecycle-work-data-transition-no-replace-unsupported")
        if error == errno.EEXIST:
            _fail("lifecycle-work-data-transition-collision")
        _fail("lifecycle-work-data-transition-rename-failed", OSError(error, os.strerror(error)))


def _observed(parent: int, name: str) -> os.stat_result | None:
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    parent_info = os.fstat(parent)
    if (
        not stat.S_ISDIR(info.st_mode) or info.st_dev != parent_info.st_dev
        or info.st_uid != os.geteuid() or info.st_gid != os.getegid()
        or stat.S_IMODE(info.st_mode) & 0o700 != 0o700
        or stat.S_IMODE(info.st_mode) & 0o7022
    ):
        _fail("lifecycle-work-data-transition-foreign-path")
    return info


def _open_child(parent: int, name: str, observed: os.stat_result) -> int:
    descriptor = os.open(name, _directory_flags() | os.O_NOATIME, dir_fd=parent)
    try:
        info = os.fstat(descriptor)
        if _identity(info) != _identity(observed) or info.st_dev != os.fstat(parent).st_dev:
            _fail("lifecycle-work-data-transition-drift")
        _validate_source_mode(info, directory=True)
        _check_extended_metadata(descriptor)
        return descriptor
    except BaseException:
        _close_quietly(descriptor)
        raise


def _record(digest: Any, value: dict[str, Any]) -> None:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("ascii")
    digest.update(len(raw).to_bytes(4, "big"))
    digest.update(raw)


def _tree_walk(descriptor: int, relative: str, depth: int,
               budget: dict[str, int], digest: Any, device: int) -> None:
    if depth > _MAX_DEPTH:
        _fail("lifecycle-work-data-transition-tree-limit")
    before = os.fstat(descriptor)
    if before.st_dev != device:
        _fail("lifecycle-work-data-transition-cross-device")
    _validate_source_mode(before, directory=True)
    _check_extended_metadata(descriptor)
    names = sorted(_validate_name(name) for name in os.listdir(descriptor))
    _record(digest, {
        "path": relative, "type": "directory", "dev": str(before.st_dev),
        "ino": str(before.st_ino), "uid": str(before.st_uid), "gid": str(before.st_gid),
        "mode": stat.S_IMODE(before.st_mode), "atimeNs": str(before.st_atime_ns),
        "mtimeNs": str(before.st_mtime_ns), "nlink": before.st_nlink,
    })
    for name in names:
        budget["entries"] += 1
        if budget["entries"] > _MAX_ENTRIES:
            _fail("lifecycle-work-data-transition-tree-limit")
        child_path = f"{relative}/{name}" if relative else name
        if len(child_path.encode("utf-8")) > 1024:
            _fail("lifecycle-work-data-transition-tree-limit")
        observed = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if observed.st_dev != device:
            _fail("lifecycle-work-data-transition-cross-device")
        if stat.S_ISDIR(observed.st_mode):
            child = _open_child(descriptor, name, observed)
            try:
                _tree_walk(child, child_path, depth + 1, budget, digest, device)
            finally:
                _close_quietly(child)
        elif stat.S_ISREG(observed.st_mode):
            mode = _validate_source_mode(observed, directory=False)
            if observed.st_nlink != 1 or not 0 <= observed.st_size <= _MAX_FILE_BYTES:
                _fail("lifecycle-work-data-transition-tree-limit")
            child = os.open(name, _file_read_flags() | os.O_NOATIME, dir_fd=descriptor)
            try:
                info = os.fstat(child)
                if _identity(info) != _identity(observed):
                    _fail("lifecycle-work-data-transition-drift")
                _check_extended_metadata(child)
                _check_sparse_file(child, info)
                content = hashlib.sha256()
                remaining = info.st_size
                while remaining:
                    chunk = os.read(child, min(remaining, 64 * 1024))
                    if not chunk:
                        _fail("lifecycle-work-data-transition-drift")
                    content.update(chunk)
                    remaining -= len(chunk)
                if os.read(child, 1) or _identity(os.fstat(child)) != _identity(info):
                    _fail("lifecycle-work-data-transition-drift")
                if _identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) != _identity(info):
                    _fail("lifecycle-work-data-transition-drift")
                budget["bytes"] += info.st_size
                if budget["bytes"] > _MAX_TOTAL_BYTES:
                    _fail("lifecycle-work-data-transition-tree-limit")
                _record(digest, {
                    "path": child_path, "type": "file", "dev": str(info.st_dev),
                    "ino": str(info.st_ino), "uid": str(info.st_uid), "gid": str(info.st_gid),
                    "mode": mode, "atimeNs": str(info.st_atime_ns),
                    "mtimeNs": str(info.st_mtime_ns), "size": info.st_size,
                    "sha256": content.hexdigest(),
                })
            finally:
                _close_quietly(child)
        else:
            _fail("lifecycle-work-data-transition-special-file")
    if _identity(os.fstat(descriptor)) != _identity(before):
        _fail("lifecycle-work-data-transition-drift")


def _tree_ref(parent: int, name: str, info: os.stat_result) -> dict[str, str]:
    descriptor = _open_child(parent, name, info)
    try:
        digest = hashlib.sha256()
        _tree_walk(descriptor, "", 0, {"entries": 0, "bytes": 0}, digest, info.st_dev)
        visible = _observed(parent, name)
        if visible is None or (visible.st_dev, visible.st_ino) != (info.st_dev, info.st_ino):
            _fail("lifecycle-work-data-transition-drift")
        return {"dev": str(info.st_dev), "ino": str(info.st_ino),
                "sha256": digest.hexdigest()}
    finally:
        _close_quietly(descriptor)


def _stage_ref(parent: int, name: str, info: os.stat_result,
               path_state: dict[str, Any]) -> dict[str, str]:
    descriptor = _open_child(parent, name, info)
    try:
        _directories, _files, children = _plan(path_state)
        _verify_tree(descriptor, path_state, children)
        visible = _observed(parent, name)
        if visible is None or (visible.st_dev, visible.st_ino) != (info.st_dev, info.st_ino):
            _fail("lifecycle-work-data-transition-drift")
        return {"dev": str(info.st_dev), "ino": str(info.st_ino)}
    finally:
        _close_quietly(descriptor)


def _ref_valid(value: Any, *, tree: bool) -> bool:
    keys = {"dev", "ino", "sha256"} if tree else {"dev", "ino"}
    return isinstance(value, dict) and set(value) == keys and all(
        isinstance(value[field], str) and 1 <= len(value[field]) <= 20
        and value[field].isdecimal() and str(int(value[field])) == value[field]
        and 0 <= int(value[field]) < (1 << 64) for field in ("dev", "ino")
    ) and (not tree or isinstance(value["sha256"], str)
           and _SHA_RE.fullmatch(value["sha256"]) is not None)


def _phase_name(key: str, phase: str) -> str:
    return f"p-{key}-{phase}.json"


def _read_exact(root: int, name: str, expected: dict[str, Any]) -> bool:
    existing = _read(root, name)
    if existing is None:
        return False
    if existing[1] != _canonical(expected):
        _fail("lifecycle-work-data-transition-marker-mismatch")
    return True


def _publish_exact(root: int, name: str, expected: dict[str, Any]) -> None:
    _publish(root, name, _canonical(expected))


class PairedDataTransition:
    """Proof-based one-path effect; not yet wired to the production host agent."""

    def __init__(self, install_dir: Path, store: StreamSnapshotStore,
                 journal: RestoreIntentJournal) -> None:
        _validate_platform()
        if not hasattr(os, "O_NOATIME") or install_dir != store.install_dir or install_dir != journal.install_dir:
            _fail("lifecycle-work-data-transition-platform-or-scope-unsupported")
        self.install_dir, self.store, self.journal = install_dir, store, journal

    def apply(self, command: LifecycleWorkCommand, service_id: str, index: int,
              *, quiesced: Callable[[], bool] | None) -> str:
        """Retain original data and install verified staged data, or restore absence."""
        _require_quiesced(quiesced)
        with self.store.open_verified(command) as (_archive, document, receipt):
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                _fail("lifecycle-work-data-transition-scope-invalid")
            service = next((item for item in document["services"] if item["serviceId"] == service_id), None)
            if service is None or index >= len(service["paths"]):
                _fail("lifecycle-work-data-transition-scope-invalid")
            path_state = service["paths"][index]
            intent = self.journal.begin(command, receipt, service_id, index, path_state)
            install = parent = root = None
            try:
                install = _open_absolute_directory(self.install_dir, private=False)
                parts = _safe_relative_parts(intent["path"])
                parent = _open_relative_directory(install, parts[:-1], missing_ok=False)
                assert parent is not None
                if stat.S_IMODE(os.fstat(parent).st_mode) != 0o700:
                    _fail("lifecycle-work-data-transition-parent-not-private")
                root = _open_absolute_directory(self.journal.journal_root, private=True)
                target_name, stage_name, quarantine_name = parts[-1], intent["stageName"], intent["quarantineName"]
                baseline = {
                    "schema": TRANSITION_SCHEMA, "key": intent["key"],
                    "transactionId": command.transaction_id, "planHash": command.plan_hash,
                    "serviceId": service_id, "pathIndex": index, "path": intent["path"],
                    "archiveSha256": receipt.archive_sha256, "indexSha256": receipt.index_sha256,
                    "sourcePresent": intent["sourcePresent"], "targetBefore": intent["targetBefore"],
                    "stageName": stage_name, "quarantineName": quarantine_name,
                    "intentSha256": hashlib.sha256(_canonical(intent)).hexdigest(),
                    "phase": "prepared",
                }
                prepared_name = _phase_name(intent["key"], "prepared")
                existing = _read(root, prepared_name)
                target = _observed(parent, target_name)
                stage = _observed(parent, stage_name)
                quarantine = _observed(parent, quarantine_name)
                if existing is None:
                    self.journal.expect_original_target(command, receipt, service_id, index, path_state)
                    if quarantine is not None or (stage is not None) != intent["sourcePresent"]:
                        _fail("lifecycle-work-data-transition-fresh-state-invalid")
                    if (target is not None) != (intent["targetBefore"] is not None):
                        _fail("lifecycle-work-data-transition-fresh-state-invalid")
                    stage_ref = _stage_ref(parent, stage_name, stage, path_state) if stage is not None else None
                    original_tree = _tree_ref(parent, target_name, target) if target is not None else None
                    baseline["stageRef"], baseline["originalTree"] = stage_ref, original_tree
                    _require_quiesced(quiesced)
                    _publish_exact(root, prepared_name, baseline)
                    prepared = baseline
                else:
                    prepared = existing[0]
                    if set(prepared) != set(baseline) | {"stageRef", "originalTree"} or any(
                        prepared.get(field) != value for field, value in baseline.items()
                    ) or (prepared["stageRef"] is None) != (not intent["sourcePresent"]) or (
                        prepared["originalTree"] is None
                    ) != (intent["targetBefore"] is None):
                        _fail("lifecycle-work-data-transition-prepared-mismatch")
                    if prepared["stageRef"] is not None and not _ref_valid(prepared["stageRef"], tree=False):
                        _fail("lifecycle-work-data-transition-prepared-mismatch")
                    if prepared["originalTree"] is not None and (
                        not _ref_valid(prepared["originalTree"], tree=True)
                        or {field: prepared["originalTree"][field] for field in ("dev", "ino")}
                        != {field: intent["targetBefore"][field] for field in ("dev", "ino")}
                    ):
                        _fail("lifecycle-work-data-transition-prepared-mismatch")
                    if existing[1] != _canonical(prepared):
                        _fail("lifecycle-work-data-transition-prepared-mismatch")
                prepared_sha = hashlib.sha256(_canonical(prepared)).hexdigest()
                quarantined_record = {"schema": TRANSITION_SCHEMA, "key": intent["key"],
                                      "phase": "quarantined", "preparedSha256": prepared_sha}
                installed_record = {"schema": TRANSITION_SCHEMA, "key": intent["key"],
                                    "phase": "installed", "preparedSha256": prepared_sha}
                quarantined_name = _phase_name(intent["key"], "quarantined")
                installed_name = _phase_name(intent["key"], "installed")
                has_quarantined = _read_exact(root, quarantined_name, quarantined_record)
                has_installed = _read_exact(root, installed_name, installed_record)
                if prepared["originalTree"] is None and has_quarantined:
                    _fail("lifecycle-work-data-transition-marker-state-conflict")

                def classify() -> str:
                    current_target = _observed(parent, target_name)
                    current_stage = _observed(parent, stage_name)
                    current_quarantine = _observed(parent, quarantine_name)
                    shape = (current_target is not None, current_stage is not None,
                             current_quarantine is not None)
                    old, source = prepared["originalTree"], prepared["stageRef"]
                    if old is not None and source is not None:
                        state = {(True, True, False): "fresh", (False, True, True): "mid",
                                 (True, False, True): "terminal"}.get(shape)
                    elif old is not None:
                        state = {(True, False, False): "fresh", (False, False, True): "terminal"}.get(shape)
                    elif source is not None:
                        state = {(False, True, False): "fresh", (True, False, False): "terminal"}.get(shape)
                    else:
                        state = "terminal" if shape == (False, False, False) else None
                    if state is None:
                        _fail("lifecycle-work-data-transition-ambiguous-state")
                    if current_stage is not None and _stage_ref(parent, stage_name, current_stage, path_state) != source:
                        _fail("lifecycle-work-data-transition-stage-drift")
                    if current_quarantine is not None and _tree_ref(parent, quarantine_name, current_quarantine) != old:
                        _fail("lifecycle-work-data-transition-quarantine-drift")
                    if current_target is not None:
                        if state == "terminal" and source is not None:
                            if _stage_ref(parent, target_name, current_target, path_state) != source:
                                _fail("lifecycle-work-data-transition-restored-drift")
                        elif self.journal._observe_target(intent["path"]) != intent["targetBefore"] or (
                            _tree_ref(parent, target_name, current_target) != old
                        ):
                            _fail("lifecycle-work-data-transition-original-drift")
                    return state

                _require_quiesced(quiesced)
                state = classify()
                if has_installed and state != "terminal":
                    _fail("lifecycle-work-data-transition-marker-state-conflict")
                if has_quarantined and state == "fresh":
                    _fail("lifecycle-work-data-transition-marker-state-conflict")
                if state == "fresh" and prepared["originalTree"] is not None:
                    _require_quiesced(quiesced)
                    if classify() != "fresh":
                        _fail("lifecycle-work-data-transition-drift")
                    os.fsync(parent)
                    _no_replace(parent, target_name, quarantine_name)
                    os.fsync(parent)
                    state = classify()
                    if state not in {"mid", "terminal"}:
                        _fail("lifecycle-work-data-transition-drift")
                if prepared["originalTree"] is not None and state in {"mid", "terminal"}:
                    if state == "terminal" and prepared["stageRef"] is not None and not has_quarantined:
                        _fail("lifecycle-work-data-transition-marker-state-conflict")
                    if not has_quarantined:
                        _publish_exact(root, quarantined_name, quarantined_record)
                        has_quarantined = True
                if state in {"fresh", "mid"} and prepared["stageRef"] is not None:
                    _require_quiesced(quiesced)
                    if classify() != state:
                        _fail("lifecycle-work-data-transition-drift")
                    os.fsync(parent)
                    _no_replace(parent, stage_name, target_name)
                    os.fsync(parent)
                    state = classify()
                    if state != "terminal":
                        _fail("lifecycle-work-data-transition-drift")
                if state != "terminal":
                    _fail("lifecycle-work-data-transition-ambiguous-state")
                _require_quiesced(quiesced)
                if classify() != "terminal":
                    _fail("lifecycle-work-data-transition-drift")
                if not has_installed:
                    _publish_exact(root, installed_name, installed_record)
                return hashlib.sha256(_canonical(installed_record)).hexdigest()
            except (OSError, DataBackupRuntimeError, RestoreJournalError,
                    StreamRestoreStageError, StreamSnapshotError) as exc:
                _fail("lifecycle-work-data-transition-unavailable", exc)
            finally:
                _close_quietly(root)
                _close_quietly(parent)
                _close_quietly(install)


__all__ = ["TRANSITION_SCHEMA", "PairedTransitionError", "PairedDataTransition"]
