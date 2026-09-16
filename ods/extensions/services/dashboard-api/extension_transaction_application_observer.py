"""Assemble a lease-bound applied prefix from current host observations.

This observer performs no lifecycle effect or receipt transition. The host
classifies each original plan-bound apply request from current files, records,
containers, and receipts; an unknown or non-prefix result is never success.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from extension_lifecycle_work_client import ApplicationObservationResult
from extension_receipted_lifecycle_adapter import build_apply_observation_request
from extension_transaction_executor import ExecutionBinding

_OBSERVABLE_STATES = frozenset({"applying", "verifying", "reconciling"})
_MAX_OPERATIONS = 64


class TransactionApplicationObservationError(RuntimeError):
    """Value-free failure; the executor must quarantine uncertain effects."""


def _fail(code: str) -> None:
    raise TransactionApplicationObservationError(code) from None


class TransactionApplicationObserver:
    """Read a complete, exact-plan classification under one active lease."""

    def __init__(
        self,
        transaction_loader: Callable[[str], dict[str, Any]],
        grant_provider: Callable[[ExecutionBinding, tuple[str, ...]], Any],
        observe_one: Callable[[Any, Any], ApplicationObservationResult],
    ) -> None:
        if not all(
            callable(item) for item in (transaction_loader, grant_provider, observe_one)
        ):
            _fail("transaction-observation-invalid-dependency")
        self._load = transaction_loader
        self._grant = grant_provider
        self._observe_one = observe_one

    def observe(self, binding: ExecutionBinding) -> dict[str, Any]:
        if not isinstance(binding, ExecutionBinding):
            _fail("transaction-observation-invalid-binding")
        try:
            transaction = self._load(binding.transaction_id)
            if (
                not isinstance(transaction, dict)
                or transaction.get("transactionId") != binding.transaction_id
                or transaction.get("state") not in _OBSERVABLE_STATES
                or not isinstance(transaction.get("envelope"), dict)
                or transaction["envelope"].get("planHash") != binding.plan_hash
                or not isinstance(transaction["envelope"].get("plan"), dict)
            ):
                _fail("transaction-observation-plan-mismatch")
            operations = transaction["envelope"]["plan"].get("operations")
            if (
                not isinstance(operations, list)
                or not 1 <= len(operations) <= _MAX_OPERATIONS
            ):
                _fail("transaction-observation-plan-mismatch")
            mutable: list[dict[str, Any]] = []
            all_ids: list[str] = []
            for operation in operations:
                if (
                    not isinstance(operation, dict)
                    or type(operation.get("serviceId")) is not str
                    or type(operation.get("action")) is not str
                    or operation["action"]
                    not in {"install", "enable", "repair", "update", "noop"}
                ):
                    _fail("transaction-observation-plan-mismatch")
                all_ids.append(operation["serviceId"])
                if operation["action"] != "noop":
                    mutable.append(operation)
            if len(all_ids) != len(set(all_ids)):
                _fail("transaction-observation-plan-mismatch")

            applied: list[str] = []
            absent_seen = False
            for operation in mutable:
                request = build_apply_observation_request(binding, operation)
                grant = self._grant(binding, request.service_ids)
                result = self._observe_one(grant, request)
                if (
                    type(result) is not ApplicationObservationResult
                    or result.service_id != request.service_ids[0]
                ):
                    _fail("transaction-observation-invalid-result")
                if result.classification == "APPLIED":
                    if absent_seen:
                        _fail("transaction-observation-non-prefix")
                    applied.append(result.service_id)
                elif result.classification == "ABSENT":
                    absent_seen = True
                else:
                    _fail("transaction-observation-invalid-result")
            return {
                "transactionId": binding.transaction_id,
                "planHash": binding.plan_hash,
                "appliedServices": applied,
            }
        except TransactionApplicationObservationError:
            raise
        except Exception:
            _fail("transaction-observation-unavailable")


__all__ = [
    "TransactionApplicationObserver",
    "TransactionApplicationObservationError",
]
