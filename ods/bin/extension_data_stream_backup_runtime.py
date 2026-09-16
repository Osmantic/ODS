"""Receipted generic, immutable extension-data backup; no live restore effect.

The selected host route requires an admitted lease-bound Docker witness for
new snapshots of the attested old/new path union. This still does not establish
a point-in-time snapshot across non-Docker or active external writers,
so neither this archive nor a green receipt qualifies generic rollback yet.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from extension_data_backup_runtime import _close_quietly, _open_absolute_directory
from extension_data_scope_contract import bind_data_scope
from extension_data_stream_snapshot import (
    StreamSnapshotReceipt, StreamSnapshotStore, _archive_name,
)
from extension_lifecycle_work import (
    LifecycleWorkCommand, LifecycleWorkError, LifecycleWorkExecutionError,
    LifecycleWorkStartedObservation, LifecycleWorkValidationError,
)


EVIDENCE_SCHEMA = "ods.extension-data-stream-backup-evidence.v1"


def _bound(command: LifecycleWorkCommand) -> None:
    if type(command) is not LifecycleWorkCommand or command.operation_key != "backup":
        raise LifecycleWorkValidationError("lifecycle-work-data-scope-mismatch") from None
    bind_data_scope(command)


def _loaded(original: LifecycleWorkCommand, loaded: object) -> LifecycleWorkCommand:
    if (
        type(loaded) is not LifecycleWorkCommand or loaded.plan_material is None
        or any(getattr(loaded, field) != getattr(original, field) for field in (
            "transaction_id", "plan_hash", "operation_key", "request_hash",
            "service_ids", "payload", "timeout_seconds",
        ))
    ):
        raise LifecycleWorkValidationError("lifecycle-work-plan-mismatch") from None
    _bound(loaded)
    return loaded


def _evidence(command: LifecycleWorkCommand, receipt: StreamSnapshotReceipt) -> str:
    if type(receipt) is not StreamSnapshotReceipt:
        raise LifecycleWorkExecutionError("lifecycle-work-data-snapshot-readback-invalid") from None
    material = {
        "schema": EVIDENCE_SCHEMA,
        "transactionId": command.transaction_id, "planHash": command.plan_hash,
        "operationKey": command.operation_key, "serviceIds": list(command.service_ids),
        "archiveSha256": receipt.archive_sha256,
        "indexSha256": receipt.index_sha256,
        "fileCount": receipt.file_count, "contentBytes": receipt.content_bytes,
    }
    return hashlib.sha256(json.dumps(
        material, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")).hexdigest()


class StreamBackupDispatcher:
    def __init__(self, store: StreamSnapshotStore) -> None:
        self._store = store

    def __call__(self, command: LifecycleWorkCommand, *,
                 witness: Callable[[], bool] | None = None) -> str:
        _bound(command)
        if not callable(witness):
            raise LifecycleWorkExecutionError(
                "lifecycle-work-data-quiescence-witness-required"
            ) from None
        return _evidence(command, self._store.backup(command, quiescence=witness))


class StreamBackupStartedObserver:
    def __init__(self, plan_loader: Callable[[LifecycleWorkCommand], LifecycleWorkCommand],
                 store: StreamSnapshotStore) -> None:
        self._plan_loader, self._store = plan_loader, store

    def __call__(self, command: LifecycleWorkCommand) -> LifecycleWorkStartedObservation:
        try:
            loaded = self._plan_loader(command)
        except LifecycleWorkError:
            raise
        except Exception as exc:
            raise LifecycleWorkExecutionError(
                "lifecycle-work-data-observation-failed"
            ) from exc
        bound = _loaded(command, loaded)
        root = _open_absolute_directory(self._store.backup_root, private=True)
        try:
            try:
                os.stat(_archive_name(bound), dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                return LifecycleWorkStartedObservation(state="missing")
            except OSError as exc:
                raise LifecycleWorkExecutionError(
                    "lifecycle-work-data-observation-failed"
                ) from exc
        finally:
            _close_quietly(root)
        # A present but malformed or mismatched archive is not "missing".
        return LifecycleWorkStartedObservation(
            state="completed", evidence_hash=_evidence(bound, self._store.verify(bound)),
        )


@dataclass(frozen=True)
class StreamBackupRuntime:
    root: Path
    store: StreamSnapshotStore
    backup_dispatcher: StreamBackupDispatcher
    backup_started_observer: StreamBackupStartedObserver


def build_stream_backup_runtime(*, install_dir: Path, data_dir: Path,
                                plan_loader: Callable[[LifecycleWorkCommand], LifecycleWorkCommand]
                                ) -> StreamBackupRuntime:
    """Compose a verified generic backup without writing or reading an archive."""
    if not callable(plan_loader):
        raise LifecycleWorkExecutionError("lifecycle-work-data-runtime-invalid") from None
    # The installer already prepares this owner-private root for the SearXNG
    # JSON canary. Generic tar names use a distinct extension and never alias it.
    root = data_dir / "assistant-first" / "data-backups"
    store = StreamSnapshotStore(install_dir, data_dir, root)
    return StreamBackupRuntime(
        root=root, store=store, backup_dispatcher=StreamBackupDispatcher(store),
        backup_started_observer=StreamBackupStartedObserver(plan_loader, store),
    )


__all__ = [
    "EVIDENCE_SCHEMA", "StreamBackupDispatcher", "StreamBackupRuntime",
    "StreamBackupStartedObserver", "build_stream_backup_runtime",
]
