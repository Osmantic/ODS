"""Crash-recoverable lockfile finalization for committed transactions."""

from __future__ import annotations

import datetime
from collections.abc import Callable, Mapping
from typing import Any

from extension_lockfile import (
    ExtensionLockfileError,
    ExtensionLockfileStore,
    build_lockfile,
)
from extension_transactions import (
    FINALIZATION_SCHEMA,
    IdempotencyConflict,
    IntegrityError,
    TransactionError,
    TransactionStore,
)


_MAX_COMMIT_ATTEMPTS = 4


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TransactionLockfileFinalizer:
    """Derive and durably receipt desired state after runtime commit.

    The transaction journal remains authoritative for runtime effects.  A
    committed journal entry may temporarily precede the lockfile, but the
    executor does not report full success until this class has committed the
    lockfile and persisted the transaction-bound finalization receipt.  Retrying
    a committed transaction never replays lifecycle effects.
    """

    def __init__(
        self,
        *,
        transactions: TransactionStore,
        lockfiles: ExtensionLockfileStore,
        observed_state: Callable[[], dict[str, Any]],
        runtime_mode: str,
        backup_reference: Callable[[Mapping[str, Any]], str | None] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._transactions = transactions
        self._lockfiles = lockfiles
        self._observed_state = observed_state
        self._runtime_mode = runtime_mode
        self._backup_reference = backup_reference or (lambda _transaction: None)
        self._clock = clock or _utc_now

    @staticmethod
    def _last_transaction(lockfile: Mapping[str, Any]) -> Mapping[str, Any]:
        return lockfile["lockfile"]["lastCommittedTransaction"]

    @staticmethod
    def _receipt(
        transaction: Mapping[str, Any],
        lockfile: Mapping[str, Any],
        recorded_at: str,
    ) -> dict[str, Any]:
        last = TransactionLockfileFinalizer._last_transaction(lockfile)
        envelope = transaction.get("envelope")
        if not isinstance(envelope, Mapping):
            raise IntegrityError("finalization-transaction-envelope")
        expected = {
            "transactionId": transaction.get("transactionId"),
            "planHash": envelope.get("planHash"),
            "sequence": transaction.get("sequence"),
        }
        if any(last.get(key) != value for key, value in expected.items()):
            raise IntegrityError("lockfile-finalization-binding-mismatch")
        return {
            "schema": FINALIZATION_SCHEMA,
            **expected,
            "lockfileHash": lockfile["lockfileHash"],
            "postCommitObservedStateRevision": lockfile["lockfile"][
                "postCommitObservedStateRevision"
            ],
            "recordedAt": recorded_at,
        }

    def _fresh_transaction(self, transaction: Mapping[str, Any]) -> dict[str, Any]:
        transaction_id = transaction.get("transactionId")
        envelope = transaction.get("envelope")
        if not isinstance(transaction_id, str) or not isinstance(envelope, Mapping):
            raise IntegrityError("invalid-finalization-transaction")
        fresh = self._transactions.read(transaction_id)
        if (
            fresh["state"] != "committed"
            or fresh["envelope"]["planHash"] != envelope.get("planHash")
            or fresh["sequence"] != transaction.get("sequence")
        ):
            raise IntegrityError("finalization-transaction-drift")
        return fresh

    def _record(
        self, transaction: Mapping[str, Any], lockfile: Mapping[str, Any]
    ) -> dict[str, Any]:
        receipt = self._receipt(transaction, lockfile, self._clock())
        try:
            stored = self._transactions.record_finalization(
                transaction["transactionId"], receipt
            )
        except IdempotencyConflict as exc:
            raise IntegrityError("lockfile-finalization-history-conflict") from exc
        stored.pop("duplicate", None)
        return stored

    def _backfill_active_receipt(
        self, active: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        if active is None:
            return None
        last = self._last_transaction(active)
        try:
            prior = self._transactions.read(last["transactionId"])
        except TransactionError as exc:
            raise IntegrityError("lockfile-history-unavailable", exc.code) from exc
        if (
            prior["state"] != "committed"
            or prior["envelope"]["planHash"] != last["planHash"]
            or prior["sequence"] != last["sequence"]
        ):
            raise IntegrityError("lockfile-history-binding-mismatch")
        existing = prior.get("finalization")
        if existing is not None:
            expected = self._receipt(prior, active, existing["recordedAt"])
            if existing != expected:
                raise IntegrityError("lockfile-finalization-history-conflict")
            return existing
        try:
            durable = self._lockfiles.confirm_durable(active["lockfile"])
            return self._record(prior, durable)
        except ExtensionLockfileError as exc:
            raise IntegrityError("lockfile-history-durability-unconfirmed", exc.code) from exc
        except IntegrityError:
            raise
        except TransactionError as exc:
            raise IntegrityError("lockfile-history-receipt-failed", exc.code) from exc

    @staticmethod
    def _verify_existing_receipt(
        transaction: Mapping[str, Any],
        active: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        receipt = transaction.get("finalization")
        if receipt is None:
            return None
        if active is None:
            raise IntegrityError("lockfile-missing-for-finalized-transaction")
        last = TransactionLockfileFinalizer._last_transaction(active)
        if last["transactionId"] == transaction["transactionId"] and (
            active["lockfileHash"] != receipt["lockfileHash"]
            or last["planHash"] != receipt["planHash"]
            or last["sequence"] != receipt["sequence"]
        ):
            raise IntegrityError("lockfile-finalization-history-conflict")
        return dict(receipt)

    def inspect(self, transaction: Mapping[str, Any]) -> dict[str, Any] | None:
        """Verify a recorded receipt against readable active lockfile custody."""

        fresh = self._fresh_transaction(transaction)
        active = self._lockfiles.read()
        return self._verify_existing_receipt(fresh, active)

    def finalize(self, transaction: Mapping[str, Any]) -> dict[str, Any]:
        """Finalize or recover one exact committed transaction."""

        fresh = self._fresh_transaction(transaction)
        active = self._lockfiles.read()
        existing = self._verify_existing_receipt(fresh, active)
        if existing is not None:
            return existing

        for _attempt in range(_MAX_COMMIT_ATTEMPTS):
            active = self._lockfiles.read()
            active_receipt = self._backfill_active_receipt(active)
            fresh = self._fresh_transaction(transaction)
            if fresh.get("finalization") is not None:
                return dict(fresh["finalization"])
            if active_receipt is not None and (
                active_receipt["transactionId"] == fresh["transactionId"]
            ):
                return dict(active_receipt)

            try:
                observed = self._observed_state()
                backup_reference = self._backup_reference(fresh)
                candidate = build_lockfile(
                    transaction=fresh,
                    observed_state=observed,
                    runtime_mode=self._runtime_mode,
                    backup_reference=backup_reference,
                    previous_lockfile=active,
                )
                committed = self._lockfiles.commit(candidate["lockfile"])
            except ExtensionLockfileError as exc:
                if exc.code in {
                    "prior-lockfile-mismatch",
                    "post-commit-observation-mismatch",
                }:
                    continue
                if exc.code == "lockfile-durability-uncertain":
                    try:
                        recovered = self._lockfiles.confirm_durable(
                            candidate["lockfile"]
                        )
                        return self._record(fresh, recovered)
                    except (ExtensionLockfileError, TransactionError) as recovery_exc:
                        recovery_code = getattr(recovery_exc, "code", "unavailable")
                        raise IntegrityError(
                            "lockfile-finalization-durability-unconfirmed",
                            recovery_code,
                        ) from recovery_exc
                raise IntegrityError("lockfile-finalization-failed", exc.code) from exc
            except IntegrityError:
                raise
            except Exception as exc:
                raise IntegrityError("lockfile-finalization-unavailable") from exc
            return self._record(fresh, committed)

        raise IntegrityError("lockfile-finalization-conflict")


__all__ = ["TransactionLockfileFinalizer"]
