"""Receipted generic data restore from a sealed stream snapshot.

The host must supply a fresh lease-bound Docker quiescence witness. A started
receipt is deliberately replayed through the same idempotent path: each
completed paired transition proves its own terminal state, while an incomplete
one resumes without discarding either the staged or quarantined tree.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable

from extension_data_backup_runtime import _close_quietly, _open_absolute_directory
from extension_data_paired_transition import PairedDataTransition, _phase_name
from extension_data_restore_journal import RestoreIntentJournal, _names, _read
from extension_data_scope_contract import bind_data_scope
from extension_data_stream_restore import StreamRestoreStager
from extension_data_stream_snapshot import StreamSnapshotStore
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkExecutionError,
    LifecycleWorkUncertainEffect,
    LifecycleWorkValidationError,
)


EVIDENCE_SCHEMA = "ods.extension-data-stream-restore-evidence.v1"


def _require_command(command: LifecycleWorkCommand) -> None:
    if type(command) is not LifecycleWorkCommand or command.operation_key != "restore":
        raise LifecycleWorkValidationError("lifecycle-work-data-scope-mismatch") from None
    bind_data_scope(command)


def _require_witness(witness: Callable[[], bool] | None) -> None:
    if not callable(witness):
        raise LifecycleWorkExecutionError(
            "lifecycle-work-data-quiescence-witness-required"
        ) from None
    try:
        if witness() is not True:
            raise LifecycleWorkUncertainEffect(
                "lifecycle-work-data-restore-not-quiesced"
            ) from None
    except LifecycleWorkUncertainEffect:
        raise
    except Exception as exc:
        raise LifecycleWorkUncertainEffect(
            "lifecycle-work-data-restore-quiescence-unavailable"
        ) from exc


def _ensure_journal_root(data_dir: Path) -> Path:
    parent_path = data_dir / "assistant-first"
    root_path = parent_path / "restore-journals"
    parent = _open_absolute_directory(parent_path, private=True)
    try:
        try:
            os.mkdir("restore-journals", 0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
    finally:
        _close_quietly(parent)
    root = _open_absolute_directory(root_path, private=True)
    _close_quietly(root)
    return root_path


def _has_prepared(journal: RestoreIntentJournal, command: LifecycleWorkCommand,
                  service_id: str, index: int, path: str) -> bool:
    key, _intent_name, _stage_name, _quarantine_name = _names(
        command, service_id, index, path
    )
    root = _open_absolute_directory(journal.journal_root, private=True)
    try:
        return _read(root, _phase_name(key, "prepared")) is not None
    finally:
        _close_quietly(root)


class StreamRestoreDispatcher:
    """Run all approved data paths under one host-owned witness.

    No live target is touched until every path without a prepared transition
    has a complete verified stage. On replay, prepared paths bypass staging:
    the original target may already be quarantined or replaced.
    """

    def __init__(self, store: StreamSnapshotStore) -> None:
        if type(store) is not StreamSnapshotStore:
            raise LifecycleWorkExecutionError("lifecycle-work-data-runtime-invalid") from None
        self._store = store

    def __call__(self, command: LifecycleWorkCommand, *,
                 witness: Callable[[], bool] | None = None) -> str:
        _require_command(command)
        _require_witness(witness)
        # Verify the whole immutable archive before any path receives a live
        # transition. The store also binds it to the exact plan and path union.
        with self._store.open_verified(command) as (_archive, document, receipt):
            paths = [
                (service["serviceId"], index, item["path"])
                for service in document["services"]
                for index, item in enumerate(service["paths"])
            ]
            try:
                root = _ensure_journal_root(self._store.data_dir)
                journal = RestoreIntentJournal(self._store.install_dir, root)
                stager = StreamRestoreStager(self._store.install_dir, self._store, journal)
                transition = PairedDataTransition(self._store.install_dir, self._store, journal)

                for service_id, index, path in paths:
                    _require_witness(witness)
                    if not _has_prepared(journal, command, service_id, index, path):
                        stager.stage(command, service_id, index)

                effects = []
                for service_id, index, _path in paths:
                    _require_witness(witness)
                    effects.append({
                        "serviceId": service_id,
                        "pathIndex": index,
                        "transitionHash": transition.apply(
                            command, service_id, index, quiesced=witness
                        ),
                    })
                _require_witness(witness)
            except LifecycleWorkUncertainEffect:
                raise
            except Exception as exc:
                # A prepared stage or a live rename may already exist. Never
                # terminalize a started receipt as failed on ambiguous replay.
                raise LifecycleWorkUncertainEffect(
                    "lifecycle-work-data-restore-uncertain"
                ) from exc

            material = {
                "schema": EVIDENCE_SCHEMA,
                "transactionId": command.transaction_id,
                "planHash": command.plan_hash,
                "operationKey": command.operation_key,
                "serviceIds": list(command.service_ids),
                "archiveSha256": receipt.archive_sha256,
                "indexSha256": receipt.index_sha256,
                "effects": effects,
            }
            return hashlib.sha256(json.dumps(
                material, ensure_ascii=True, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("ascii")).hexdigest()


__all__ = ["EVIDENCE_SCHEMA", "StreamRestoreDispatcher"]
