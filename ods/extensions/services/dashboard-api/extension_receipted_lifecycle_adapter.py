"""Dormant receipt boundary for synchronous leased lifecycle work.

This adapter composes an already-active transaction-wide lease scope with the
host receipt client.  It never acquires custody, supplies no production worker,
and is not wired into the production executor.  A worker is called at most once
per adapter invocation and only after a started receipt is established.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from extension_lease_client import LeaseGrant
from extension_lease_lock_factory import ExtensionLeaseLockFactory
from extension_lifecycle_receipt_client import (
    ExtensionLifecycleReceiptClient,
    LifecycleReceiptClientError,
    ReceiptResult,
    SnapshotResult,
)
from extension_transaction_executor import ExecutionBinding

REQUEST_SCHEMA = "ods.extension-lifecycle-work-request.v1"
FAILURE_SCHEMA = "ods.extension-lifecycle-work-failure.v1"
MAX_WORK_REQUEST_BYTES = 32 * 1024

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SAFE_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,95}$")


class ReceiptedLifecycleAdapterError(RuntimeError):
    """Stable value-safe adapter failure."""

    def __init__(
        self, code: str, *, retryable: bool = False, ambiguous: bool = False
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous

    def __repr__(self) -> str:
        return (
            "ReceiptedLifecycleAdapterError("
            f"{self.code!r}, retryable={self.retryable!r}, "
            f"ambiguous={self.ambiguous!r})"
        )


@dataclass(frozen=True)
class LifecycleWorkRequest:
    binding: ExecutionBinding
    operation_key: str
    service_ids: tuple[str, ...]
    request_hash: str
    payload: dict[str, Any] = field(repr=False)

    def __repr__(self) -> str:
        return (
            "LifecycleWorkRequest("
            f"binding={self.binding!r}, operation_key={self.operation_key!r}, "
            f"service_ids={self.service_ids!r}, "
            f"request_hash={self.request_hash!r}, payload=<redacted>)"
        )


@dataclass(frozen=True)
class LifecycleWorkResult:
    evidence_hash: str


_Worker = Callable[[LeaseGrant, LifecycleWorkRequest], LifecycleWorkResult]


def _fail(
    code: str, *, retryable: bool = False, ambiguous: bool = False
) -> None:
    raise ReceiptedLifecycleAdapterError(
        code, retryable=retryable, ambiguous=ambiguous
    ) from None


def _service_ids(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        _fail("lifecycle-invalid-service-ids")
    result = tuple(values)
    if any(
        not isinstance(service_id, str)
        or len(service_id) > 128
        or _SERVICE_ID_RE.fullmatch(service_id) is None
        for service_id in result
    ):
        _fail("lifecycle-invalid-service-ids")
    if len(set(result)) != len(result):
        _fail("lifecycle-invalid-service-ids")
    return result


def _operation(value: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(value, dict):
        _fail("lifecycle-invalid-operation")
    service_id = value.get("serviceId")
    if (
        not isinstance(service_id, str)
        or len(service_id) > 128
        or _SERVICE_ID_RE.fullmatch(service_id) is None
    ):
        _fail("lifecycle-invalid-operation")
    cloned = _canonical_clone(value)
    if not isinstance(cloned, dict):
        _fail("lifecycle-invalid-operation")
    return service_id, cloned


def _operations(values: Any) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    if not isinstance(values, list):
        _fail("lifecycle-invalid-operations")
    service_ids: list[str] = []
    operations: list[dict[str, Any]] = []
    for value in values:
        service_id, operation = _operation(value)
        service_ids.append(service_id)
        operations.append(operation)
    if len(set(service_ids)) != len(service_ids):
        _fail("lifecycle-invalid-operations")
    return tuple(service_ids), operations


def _canonical_clone(value: Any, *, _depth: int = 0) -> Any:
    if _depth > 16:
        _fail("lifecycle-invalid-payload")
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, list):
        return [_canonical_clone(item, _depth=_depth + 1) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            _fail("lifecycle-invalid-payload")
        return {
            key: _canonical_clone(item, _depth=_depth + 1)
            for key, item in value.items()
        }
    _fail("lifecycle-invalid-payload")


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    cloned = _canonical_clone(value)
    try:
        encoded = json.dumps(
            cloned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("lifecycle-invalid-payload")
    if len(encoded) > MAX_WORK_REQUEST_BYTES:
        _fail("lifecycle-work-request-size")
    return encoded


def _hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _worker_failure(error: Exception) -> tuple[str, bool, bool]:
    raw_code = getattr(error, "code", None)
    code = raw_code if isinstance(raw_code, str) else "worker-internal"
    if _SAFE_CODE_RE.fullmatch(code) is None:
        code = "worker-internal"
    return (
        code,
        bool(getattr(error, "retryable", False)),
        bool(getattr(error, "ambiguous", False)),
    )


class ReceiptedLifecycleAdapter:
    """Run one synchronous leased operation behind immutable receipts."""

    def __init__(
        self,
        custody: ExtensionLeaseLockFactory,
        receipts: ExtensionLifecycleReceiptClient,
        worker: _Worker,
    ) -> None:
        if not isinstance(custody, ExtensionLeaseLockFactory):
            _fail("lifecycle-invalid-custody")
        if not isinstance(receipts, ExtensionLifecycleReceiptClient):
            _fail("lifecycle-invalid-receipt-client")
        if not callable(worker):
            _fail("lifecycle-invalid-worker")
        self._custody = custody
        self._receipts = receipts
        self._worker = worker

    def reserve(
        self, binding: ExecutionBinding, operation: dict[str, str]
    ) -> dict[str, Any]:
        return self._one(binding, "reserve", operation)

    def download_and_verify_all(
        self, binding: ExecutionBinding, operations: list[dict[str, str]]
    ) -> dict[str, Any]:
        return self._many(binding, "download-and-verify", operations)

    def stage_all(
        self, binding: ExecutionBinding, operations: list[dict[str, str]]
    ) -> dict[str, Any]:
        return self._many(binding, "stage", operations)

    def backup_all(
        self, binding: ExecutionBinding, service_ids: list[str]
    ) -> dict[str, Any]:
        return self._services(binding, "backup", service_ids)

    def configure_all(
        self, binding: ExecutionBinding, service_ids: list[str]
    ) -> dict[str, Any]:
        return self._services(binding, "configure", service_ids)

    def apply_one(
        self, binding: ExecutionBinding, operation: dict[str, str]
    ) -> dict[str, Any]:
        return self._one(binding, "apply", operation)

    def verify_all(
        self, binding: ExecutionBinding, service_ids: list[str]
    ) -> dict[str, Any]:
        return self._services(binding, "verify", service_ids)

    def compensate_one(
        self, binding: ExecutionBinding, operation: dict[str, str]
    ) -> dict[str, Any]:
        return self._one(binding, "compensate", operation)

    def restore_all(
        self, binding: ExecutionBinding, service_ids: list[str]
    ) -> dict[str, Any]:
        return self._services(binding, "restore", service_ids)

    def release(
        self, binding: ExecutionBinding, service_ids: list[str]
    ) -> dict[str, Any]:
        return self._services(binding, "release", service_ids)

    def _one(
        self,
        binding: ExecutionBinding,
        prefix: str,
        operation: dict[str, Any],
    ) -> dict[str, Any]:
        service_id, cloned = _operation(operation)
        return self._run(
            binding,
            f"{prefix}:{service_id}",
            (service_id,),
            {"operation": cloned},
        )

    def _many(
        self,
        binding: ExecutionBinding,
        operation_key: str,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        service_ids, cloned = _operations(operations)
        return self._run(
            binding,
            operation_key,
            service_ids,
            {"operations": cloned},
        )

    def _services(
        self,
        binding: ExecutionBinding,
        operation_key: str,
        service_ids: list[str],
    ) -> dict[str, Any]:
        validated = _service_ids(service_ids)
        return self._run(
            binding,
            operation_key,
            validated,
            {"serviceIds": list(validated)},
        )

    def _run(
        self,
        binding: ExecutionBinding,
        operation_key: str,
        service_ids: tuple[str, ...],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(binding, ExecutionBinding):
            _fail("lifecycle-invalid-binding")
        request = {
            "schema": REQUEST_SCHEMA,
            "transactionId": binding.transaction_id,
            "planHash": binding.plan_hash,
            "operationKey": operation_key,
            "serviceIds": list(service_ids),
            "payload": payload,
        }
        request_hash = _hash(request)
        if not service_ids:
            return self._completion(
                binding,
                operation_key,
                service_ids,
                request_hash,
                _hash({**request, "outcome": "no-op"}),
            )

        grant = self._custody.current_grant(binding, service_ids)
        try:
            initial = self._receipts.snapshot(
                binding.transaction_id, binding.plan_hash, operation_key
            )
        except LifecycleReceiptClientError:
            raise
        except Exception:
            _fail("lifecycle-receipt-client-internal", retryable=True)
        replay = self._initial_snapshot(
            binding, initial, operation_key, request_hash, service_ids
        )
        if replay is not None:
            return replay

        began = self._begin(
            binding, operation_key, request_hash, service_ids
        )
        if began.kind != "started":
            _fail("lifecycle-receipt-begin-conflict")

        work = LifecycleWorkRequest(
            binding=binding,
            operation_key=operation_key,
            service_ids=service_ids,
            request_hash=request_hash,
            payload=_canonical_clone(payload),
        )
        try:
            result = self._worker(grant, work)
            if (
                not isinstance(result, LifecycleWorkResult)
                or not isinstance(result.evidence_hash, str)
                or _HASH_RE.fullmatch(result.evidence_hash) is None
            ):
                raise ReceiptedLifecycleAdapterError(
                    "worker-invalid-result"
                )
        except Exception as worker_error:
            code, retryable, ambiguous = _worker_failure(worker_error)
            if ambiguous:
                observed = self._snapshot_after_ambiguity(
                    binding, operation_key
                )
                if observed.state == "absent":
                    _fail("lifecycle-receipt-ambiguous", ambiguous=True)
                self._require_snapshot_binding(
                    observed, request_hash, service_ids
                )
                if observed.state == "completed":
                    terminal = observed.terminal_receipt
                    if terminal is None:
                        _fail(
                            "lifecycle-receipt-invalid-snapshot",
                            ambiguous=True,
                        )
                    return self._from_terminal(binding, terminal)
                if observed.state == "failed":
                    _fail("lifecycle-receipt-terminal-failed")
                if observed.state == "started":
                    _fail(
                        "lifecycle-receipt-recovery-required",
                        ambiguous=True,
                    )
                _fail("lifecycle-receipt-invalid-snapshot", ambiguous=True)
            evidence_hash = _hash(
                {
                    "schema": FAILURE_SCHEMA,
                    "transactionId": binding.transaction_id,
                    "planHash": binding.plan_hash,
                    "operationKey": operation_key,
                    "requestHash": request_hash,
                    "serviceIds": list(service_ids),
                    "outcome": "failed",
                    "code": code,
                }
            )
            try:
                self._finish(
                    binding,
                    operation_key,
                    request_hash,
                    service_ids,
                    "failed",
                    evidence_hash,
                )
            except Exception as receipt_error:
                raise receipt_error from None
            _fail(
                f"lifecycle-{code}",
                retryable=retryable,
                ambiguous=ambiguous,
            )

        terminal = self._finish(
            binding,
            operation_key,
            request_hash,
            service_ids,
            "completed",
            result.evidence_hash,
        )
        return self._from_terminal(binding, terminal)

    def _initial_snapshot(
        self,
        binding: ExecutionBinding,
        snapshot: SnapshotResult,
        operation_key: str,
        request_hash: str,
        service_ids: tuple[str, ...],
    ) -> dict[str, Any] | None:
        if snapshot.state == "absent":
            return None
        self._require_snapshot_binding(snapshot, request_hash, service_ids)
        if snapshot.state == "started":
            _fail("lifecycle-receipt-recovery-required", ambiguous=True)
        terminal = snapshot.terminal_receipt
        if terminal is None:
            _fail("lifecycle-receipt-invalid-snapshot", ambiguous=True)
        if snapshot.state == "failed":
            _fail("lifecycle-receipt-terminal-failed")
        if snapshot.state != "completed":
            _fail("lifecycle-receipt-invalid-snapshot", ambiguous=True)
        if (
            snapshot.transaction_id != binding.transaction_id
            or snapshot.plan_hash != binding.plan_hash
            or snapshot.operation_key != operation_key
        ):
            _fail("lifecycle-receipt-binding-mismatch")
        return self._from_terminal(binding, terminal)

    @staticmethod
    def _require_snapshot_binding(
        snapshot: SnapshotResult,
        request_hash: str,
        service_ids: tuple[str, ...],
    ) -> None:
        started = snapshot.started_receipt
        if (
            started is None
            or started.request_hash != request_hash
            or started.service_ids != service_ids
        ):
            _fail("lifecycle-receipt-request-mismatch")

    def _begin(
        self,
        binding: ExecutionBinding,
        operation_key: str,
        request_hash: str,
        service_ids: tuple[str, ...],
    ) -> ReceiptResult:
        for attempt in range(2):
            try:
                result = self._receipts.begin(
                    binding.transaction_id,
                    binding.plan_hash,
                    operation_key,
                    request_hash,
                    service_ids,
                )
                return result
            except LifecycleReceiptClientError as error:
                if not error.ambiguous:
                    raise
                snapshot = self._snapshot_after_ambiguity(binding, operation_key)
                if snapshot.state == "started":
                    self._require_snapshot_binding(
                        snapshot, request_hash, service_ids
                    )
                    started = snapshot.started_receipt
                    if started is None:
                        _fail("lifecycle-receipt-invalid-snapshot", ambiguous=True)
                    return started
                if snapshot.state != "absent":
                    _fail("lifecycle-receipt-begin-conflict", ambiguous=True)
                if attempt == 1:
                    _fail("lifecycle-receipt-ambiguous", ambiguous=True)
            except Exception:
                _fail("lifecycle-receipt-ambiguous", ambiguous=True)
        _fail("lifecycle-receipt-ambiguous", ambiguous=True)

    def _finish(
        self,
        binding: ExecutionBinding,
        operation_key: str,
        request_hash: str,
        service_ids: tuple[str, ...],
        outcome: str,
        evidence_hash: str,
    ) -> ReceiptResult:
        for attempt in range(2):
            try:
                terminal = self._receipts.finish(
                    binding.transaction_id,
                    binding.plan_hash,
                    operation_key,
                    request_hash,
                    service_ids,
                    outcome,
                    evidence_hash,
                )
                if (
                    terminal.outcome != outcome
                    or terminal.evidence_hash != evidence_hash
                ):
                    _fail("lifecycle-receipt-finish-conflict", ambiguous=True)
                return terminal
            except LifecycleReceiptClientError as error:
                if not error.ambiguous:
                    raise
                snapshot = self._snapshot_after_ambiguity(binding, operation_key)
                if snapshot.state == "absent":
                    _fail("lifecycle-receipt-ambiguous", ambiguous=True)
                self._require_snapshot_binding(snapshot, request_hash, service_ids)
                if snapshot.state in {"completed", "failed"}:
                    terminal = snapshot.terminal_receipt
                    if (
                        terminal is None
                        or terminal.outcome != outcome
                        or terminal.evidence_hash != evidence_hash
                    ):
                        _fail("lifecycle-receipt-finish-conflict", ambiguous=True)
                    return terminal
                if snapshot.state != "started" or attempt == 1:
                    _fail("lifecycle-receipt-ambiguous", ambiguous=True)
            except ReceiptedLifecycleAdapterError:
                raise
            except Exception:
                _fail("lifecycle-receipt-ambiguous", ambiguous=True)
        _fail("lifecycle-receipt-ambiguous", ambiguous=True)

    def _snapshot_after_ambiguity(
        self, binding: ExecutionBinding, operation_key: str
    ) -> SnapshotResult:
        try:
            return self._receipts.snapshot(
                binding.transaction_id, binding.plan_hash, operation_key
            )
        except Exception:
            _fail("lifecycle-receipt-reconciliation-failed", ambiguous=True)

    @staticmethod
    def _from_terminal(
        binding: ExecutionBinding, terminal: ReceiptResult
    ) -> dict[str, Any]:
        if terminal.outcome != "completed" or terminal.evidence_hash is None:
            _fail("lifecycle-receipt-terminal-failed")
        return ReceiptedLifecycleAdapter._completion(
            binding,
            terminal.operation_key,
            terminal.service_ids,
            terminal.request_hash,
            terminal.evidence_hash,
            event_hash=terminal.event_hash,
        )

    @staticmethod
    def _completion(
        binding: ExecutionBinding,
        operation_key: str,
        service_ids: tuple[str, ...],
        request_hash: str,
        evidence_hash: str,
        *,
        event_hash: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": True,
            "completed": True,
            "transactionId": binding.transaction_id,
            "planHash": binding.plan_hash,
            "operationKey": operation_key,
            "serviceIds": list(service_ids),
            "requestHash": request_hash,
            "evidenceHash": evidence_hash,
            "outcome": "completed",
        }
        if event_hash is not None:
            result["eventHash"] = event_hash
        return result


__all__ = [
    "LifecycleWorkRequest",
    "LifecycleWorkResult",
    "ReceiptedLifecycleAdapter",
    "ReceiptedLifecycleAdapterError",
]
