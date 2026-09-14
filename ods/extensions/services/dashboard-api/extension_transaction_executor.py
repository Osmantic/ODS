"""Crash-safe orchestration for approved extension transactions.

The assistant-facing boundary supplies only an immutable transaction ID and
plan hash. The executor reloads the stored plan, locks every selected service
in canonical order, revalidates provenance, and advances the durable state
machine through injected lifecycle adapters. Approval and purge are
deliberately absent from this module.
"""

from __future__ import annotations

import datetime
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from extension_transactions import (
    TERMINAL_STATES,
    IntegrityError,
    TransitionError,
    ValidationRejected,
)


EXECUTION_STEPS = (
    "reserved",
    "downloading",
    "staged",
    "configuring",
    "applying",
    "verifying",
    "committed",
)
ACTIVE_STATES = frozenset(EXECUTION_STEPS[:-1])
RECOVERY_STATES = frozenset({"failed", "reconciling"})


class ProvenanceVerifier(Protocol):
    """Revalidate current revisions and catalog definition hashes exactly."""

    def verify(self, plan_hash: str, envelope: dict[str, Any]) -> bool: ...


class ServiceLock(Protocol):
    def __enter__(self) -> Any: ...

    def __exit__(self, *exc: Any) -> None: ...


class ServiceLockFactory(Protocol):
    """Return a cross-process lock over all supplied service IDs."""

    def lock_services(self, service_ids: list[str]) -> ServiceLock: ...


class LifecycleAdapter(Protocol):
    """Idempotent lifecycle operations with explicit completion evidence.

    Every method returns a mapping containing both ``ok: true`` and
    ``completed: true`` only after the requested work has completed. A queued
    job or HTTP 202 acknowledgement is never completion. ``backup_all`` must
    preserve the first pre-transaction backup when replayed after a crash.
    """

    def reserve(self, operation: dict[str, str]) -> dict[str, Any]: ...

    def download_and_verify_all(
        self, operations: list[dict[str, str]]
    ) -> dict[str, Any]: ...

    def stage_all(self, operations: list[dict[str, str]]) -> dict[str, Any]: ...

    def backup_all(self, service_ids: list[str]) -> dict[str, Any]: ...

    def configure_all(self, service_ids: list[str]) -> dict[str, Any]: ...

    def apply_one(self, operation: dict[str, str]) -> dict[str, Any]: ...

    def verify_all(self, service_ids: list[str]) -> dict[str, Any]: ...

    def compensate_one(self, operation: dict[str, str]) -> dict[str, Any]: ...

    def restore_all(self, service_ids: list[str]) -> dict[str, Any]: ...

    def release(self, service_ids: list[str]) -> dict[str, Any]: ...


class ObservationAdapter(Protocol):
    """Read durable host evidence, never transient progress UI state."""

    def observe(self, transaction_id: str) -> dict[str, Any]:
        """Return exactly ``{"appliedServices": [IDs in plan order]}``."""
        ...


@dataclass(frozen=True)
class ExecuteResult:
    transaction_id: str
    final_state: str
    sequence: int
    applied_services: list[str] = field(default_factory=list)
    error: str | None = None


class TransactionExecutor:
    """Drive or reconcile one exact, owner-approved transaction."""

    def __init__(
        self,
        store: Any,
        verifier: ProvenanceVerifier,
        lock_factory: ServiceLockFactory,
        adapter: LifecycleAdapter,
        observer: ObservationAdapter,
        actor: str,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._verifier = verifier
        self._lock_factory = lock_factory
        self._adapter = adapter
        self._observer = observer
        self._actor = actor
        self._clock = clock or _now_ts
        self._process_lock = threading.Lock()

    def execute(self, transaction_id: str, plan_hash: str) -> ExecuteResult:
        """Execute or resume an exact stored transaction.

        The method accepts no operations, approval material, configuration,
        secrets, shell commands, or purge intent.
        """
        with self._process_lock:
            initial = self._load_exact(transaction_id, plan_hash)
            if initial["state"] in TERMINAL_STATES:
                return self._result(initial)

            envelope = initial["envelope"]
            operations = [dict(op) for op in envelope["plan"]["operations"]]
            service_ids = sorted({op["serviceId"] for op in operations})

            # Lock all services before the final state/provenance check so a
            # concurrent single-service workflow cannot invalidate the plan
            # between verification and the first mutation.
            with self._lock_factory.lock_services(service_ids):
                loaded = self._load_exact(transaction_id, plan_hash)
                state = loaded["state"]
                if state in TERMINAL_STATES:
                    return self._result(loaded)
                if state in RECOVERY_STATES:
                    return self._reconcile(transaction_id, operations, service_ids)
                if state != "approved" and state not in ACTIVE_STATES:
                    raise TransitionError("invalid-start-state", state)

                try:
                    provenance_ok = self._verifier.verify(
                        plan_hash, loaded["envelope"]
                    )
                except Exception as exc:
                    if state == "approved":
                        raise TransitionError("provenance-check-failed") from exc
                    return self._fail_and_reconcile(
                        transaction_id,
                        operations,
                        service_ids,
                        "provenance-check-failed",
                    )
                if provenance_ok is not True:
                    if state == "approved":
                        raise TransitionError("provenance-mismatch")
                    return self._fail_and_reconcile(
                        transaction_id,
                        operations,
                        service_ids,
                        "provenance-mismatch",
                    )

                if state == "approved":
                    self._transition(
                        transaction_id,
                        "reserved",
                        step="reserve",
                        status="started",
                    )
                    state = "reserved"

                return self._drive(transaction_id, state, operations, service_ids)

    def _load_exact(self, transaction_id: str, plan_hash: str) -> dict[str, Any]:
        loaded = self._store.read(transaction_id)
        stored_hash = loaded["envelope"]["planHash"]
        if stored_hash != plan_hash:
            raise ValidationRejected(
                "plan-hash-mismatch", f"expected {stored_hash}, got {plan_hash}"
            )
        return loaded

    def _drive(
        self,
        transaction_id: str,
        state: str,
        operations: list[dict[str, str]],
        service_ids: list[str],
    ) -> ExecuteResult:
        while state != "committed":
            try:
                next_state = self._complete_phase(
                    transaction_id, state, operations, service_ids
                )
                self._transition(
                    transaction_id,
                    next_state,
                    step=state,
                    status="completed",
                )
                state = next_state
            except Exception as exc:
                return self._fail_and_reconcile(
                    transaction_id,
                    operations,
                    service_ids,
                    _safe_failure_code(state, exc),
                )

        loaded = self._store.read(transaction_id)
        mutable_ops = [op for op in operations if op["action"] != "noop"]
        return self._result(
            loaded,
            applied_services=[op["serviceId"] for op in mutable_ops],
        )

    def _complete_phase(
        self,
        transaction_id: str,
        state: str,
        operations: list[dict[str, str]],
        service_ids: list[str],
    ) -> str:
        mutable_ops = [op for op in operations if op["action"] != "noop"]
        mutable_service_ids = [op["serviceId"] for op in mutable_ops]

        if state == "reserved":
            for operation in mutable_ops:
                self._require_completed(
                    self._adapter.reserve(dict(operation)), "reserve"
                )
            return "downloading"

        if state == "downloading":
            self._require_completed(
                self._adapter.download_and_verify_all(
                    [dict(op) for op in mutable_ops]
                ),
                "download-and-verify",
            )
            return "staged"

        if state == "staged":
            self._require_completed(
                self._adapter.stage_all([dict(op) for op in mutable_ops]), "stage"
            )
            return "configuring"

        if state == "configuring":
            self._require_completed(
                self._adapter.backup_all(list(mutable_service_ids)), "backup"
            )
            self._require_completed(
                self._adapter.configure_all(list(mutable_service_ids)), "configure"
            )
            return "applying"

        if state == "applying":
            applied = self._observed_applied(transaction_id, mutable_ops)
            for index, operation in enumerate(mutable_ops):
                service_id = operation["serviceId"]
                if index < len(applied):
                    continue
                self._require_completed(
                    self._adapter.apply_one(dict(operation)), "apply"
                )
                applied = self._observed_applied(transaction_id, mutable_ops)
                expected = [op["serviceId"] for op in mutable_ops[: index + 1]]
                if applied != expected:
                    raise IntegrityError(
                        "missing-durable-apply-evidence", service_id
                    )
            return "verifying"

        if state == "verifying":
            applied = self._observed_applied(transaction_id, mutable_ops)
            expected = [op["serviceId"] for op in mutable_ops]
            if applied != expected:
                raise IntegrityError("incomplete-apply-before-verify")
            self._require_completed(
                self._adapter.verify_all(list(service_ids)), "verify"
            )
            self._require_completed(
                self._adapter.release(list(mutable_service_ids)), "release"
            )
            return "committed"

        raise TransitionError("unknown-execution-state", state)

    def _fail_and_reconcile(
        self,
        transaction_id: str,
        operations: list[dict[str, str]],
        service_ids: list[str],
        failure_code: str,
    ) -> ExecuteResult:
        loaded = self._store.read(transaction_id)
        state = loaded["state"]
        if state in TERMINAL_STATES:
            return self._result(loaded)
        if state not in RECOVERY_STATES:
            self._transition(
                transaction_id,
                "failed",
                step="execute",
                status="failed",
                detail=failure_code,
            )
        return self._reconcile(
            transaction_id,
            operations,
            service_ids,
            failure_code=failure_code,
        )

    def _reconcile(
        self,
        transaction_id: str,
        operations: list[dict[str, str]],
        service_ids: list[str],
        failure_code: str = "interrupted-execution",
    ) -> ExecuteResult:
        loaded = self._store.read(transaction_id)
        if loaded["state"] == "failed":
            self._transition(
                transaction_id,
                "reconciling",
                step="reconcile",
                status="started",
            )
        elif loaded["state"] != "reconciling":
            raise TransitionError("invalid-recovery-state", loaded["state"])

        mutable_ops = [op for op in operations if op["action"] != "noop"]
        mutable_service_ids = [op["serviceId"] for op in mutable_ops]
        recovery_ok = True
        try:
            applied = self._observed_applied(transaction_id, mutable_ops)
        except Exception:
            applied = []
            recovery_ok = False

        by_service = {op["serviceId"]: op for op in mutable_ops}
        for service_id in reversed(applied):
            try:
                self._require_completed(
                    self._adapter.compensate_one(dict(by_service[service_id])),
                    "compensate",
                )
                remaining = self._observed_applied(transaction_id, mutable_ops)
                if service_id in remaining:
                    raise IntegrityError(
                        "missing-durable-compensation-evidence", service_id
                    )
            except Exception:
                recovery_ok = False

        for operation_name, call in (
            (
                "restore",
                lambda: self._adapter.restore_all(list(mutable_service_ids)),
            ),
            ("release", lambda: self._adapter.release(list(mutable_service_ids))),
        ):
            try:
                self._require_completed(call(), operation_name)
            except Exception:
                recovery_ok = False

        final_state = "rolled_back" if recovery_ok else "manual_recovery_required"
        self._transition(
            transaction_id,
            final_state,
            step="reconcile",
            status="completed" if recovery_ok else "manual-required",
        )
        loaded = self._store.read(transaction_id)
        return self._result(loaded, applied_services=applied, error=failure_code)

    def _observed_applied(
        self, transaction_id: str, operations: list[dict[str, str]]
    ) -> list[str]:
        evidence = self._observer.observe(transaction_id)
        if not isinstance(evidence, dict) or set(evidence) != {"appliedServices"}:
            raise IntegrityError("invalid-observation-shape")
        applied = evidence["appliedServices"]
        if not isinstance(applied, list) or any(
            not isinstance(service_id, str) for service_id in applied
        ):
            raise IntegrityError("invalid-applied-services")
        if len(set(applied)) != len(applied):
            raise IntegrityError("duplicate-applied-service")
        operation_order = [op["serviceId"] for op in operations]
        if applied != operation_order[: len(applied)]:
            raise IntegrityError("non-prefix-applied-services")
        return list(applied)

    @staticmethod
    def _require_completed(result: dict[str, Any], operation: str) -> None:
        if not isinstance(result, dict):
            raise TransitionError("adapter-incomplete", operation)
        if result.get("statusCode") in {202, "202"} or result.get("accepted") is True:
            raise TransitionError("adapter-background-ack", operation)
        if result.get("ok") is not True or result.get("completed") is not True:
            raise TransitionError("adapter-incomplete", operation)

    def _transition(
        self,
        transaction_id: str,
        target_state: str,
        *,
        step: str,
        status: str,
        detail: str | None = None,
    ) -> dict[str, Any]:
        timestamp = self._clock()
        step_metadata: dict[str, str] = {"step": step, "status": status}
        if detail is not None:
            step_metadata["detail"] = detail[:256]
        return self._store.transition(
            transaction_id,
            target_state,
            self._actor,
            timestamp,
            timestamp,
            {"step": step_metadata},
        )

    @staticmethod
    def _result(
        loaded: dict[str, Any],
        *,
        applied_services: list[str] | None = None,
        error: str | None = None,
    ) -> ExecuteResult:
        return ExecuteResult(
            transaction_id=loaded["transactionId"],
            final_state=loaded["state"],
            sequence=loaded["sequence"],
            applied_services=list(applied_services or []),
            error=error,
        )


def _safe_failure_code(state: str, exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        safe = "".join(ch for ch in code if ch.isalnum() or ch in "-_")[:96]
        if safe:
            return f"{state}:{safe}"
    return f"{state}:{type(exc).__name__}"


def _now_ts() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
