"""Transactional bridge from typed configuration to host-owned secret custody."""

from __future__ import annotations

from typing import Any

from assistant_first_secret_client import SecretCustodyError
from extension_configuration import (
    ExtensionConfigurationError,
    configuration_schema,
    validate_configuration_submission,
    validate_stored_configuration,
)
from extension_transactions import (
    IntegrityError,
    TransitionError,
    ValidationRejected,
)


class TransactionConfigurationManager:
    """Validate, stage, persist, and reverify one immutable configuration."""

    def __init__(self, store: Any, secret_custodian: Any, clock: Any) -> None:
        self._store = store
        self._secret_custodian = secret_custodian
        self._clock = clock

    def view(self, transaction_id: str) -> dict[str, Any]:
        loaded = self._store.read(transaction_id)
        envelope = loaded["envelope"]
        plan_hash = envelope["planHash"]
        schema = self._schema(envelope, plan_hash, integrity=True)
        record = loaded.get("configuration")
        if record is None:
            return {
                "schema": "ods.assistant-first.transaction-configuration-view.v1",
                "transactionId": transaction_id,
                "planHash": plan_hash,
                "schemaHash": schema["schemaHash"],
                "fields": schema["fields"],
                "configured": False,
                "values": {},
                "presentConfigKeys": [],
                "presentSecretKeys": [],
                "appliedDefaultKeys": [],
            }
        self._verify_record(loaded, schema)
        return self._project(record, schema, duplicate=None)

    def submit(
        self,
        transaction_id: str,
        *,
        plan_hash: str,
        schema_hash: str,
        idempotency_key: str,
        values: dict[str, Any],
        secret_values: dict[str, Any],
    ) -> dict[str, Any]:
        loaded = self._store.read(transaction_id)
        if loaded["state"] != "awaiting_approval":
            raise TransitionError("configuration-locked", loaded["state"])
        envelope = loaded["envelope"]
        if envelope["planHash"] != plan_hash:
            raise ValidationRejected("plan-hash-mismatch")
        schema = self._schema(envelope, plan_hash, integrity=False)
        if schema["schemaHash"] != schema_hash:
            raise ValidationRejected("schema-hash-mismatch")
        try:
            receipt = validate_configuration_submission(
                envelope,
                values,
                secret_values,
                expected_plan_hash=plan_hash,
            )
        except ExtensionConfigurationError as exc:
            raise ValidationRejected(exc.code) from None
        if receipt["presentSecretKeys"] and self._secret_custodian is None:
            raise IntegrityError("secret-custody-unavailable")
        timestamp = self._clock()
        intent = self._store.begin_configuration_exact(
            transaction_id,
            plan_hash,
            schema_hash,
            idempotency_key,
            values,
            receipt["presentSecretKeys"],
            receipt["appliedDefaultKeys"],
            timestamp,
            timestamp,
        )

        reference = None
        custody_duplicate = True
        if receipt["presentSecretKeys"]:
            try:
                staged = self._secret_custodian.stage(
                    transaction_id=transaction_id,
                    plan_hash=plan_hash,
                    schema_hash=schema_hash,
                    idempotency_key=idempotency_key,
                    secret_values=secret_values,
                )
            except SecretCustodyError as exc:
                raise IntegrityError(exc.code) from None
            if staged.get("presentSecretKeys") != receipt["presentSecretKeys"]:
                raise IntegrityError("secret-custody-presence-mismatch")
            reference = staged.get("reference")
            custody_duplicate = staged.get("duplicate") is True

        finished = self._store.finish_configuration_exact(
            transaction_id,
            plan_hash,
            idempotency_key,
            reference,
        )
        duplicate = (
            intent.get("duplicate") is True
            and custody_duplicate
            and finished.get("duplicate") is True
        )
        return self._project(finished, schema, duplicate=duplicate)

    def require_ready(
        self, transaction_id: str, plan_hash: str
    ) -> dict[str, Any]:
        loaded = self._store.read(transaction_id)
        envelope = loaded["envelope"]
        if envelope["planHash"] != plan_hash:
            raise ValidationRejected("plan-hash-mismatch")
        schema = self._schema(envelope, plan_hash, integrity=True)
        record = loaded.get("configuration")
        if record is None:
            raise TransitionError("missing-configuration")
        self._verify_record(loaded, schema)
        if record["presentSecretKeys"]:
            if self._secret_custodian is None:
                raise IntegrityError("secret-custody-unavailable")
            try:
                status = self._secret_custodian.status(
                    transaction_id=transaction_id,
                    plan_hash=plan_hash,
                    schema_hash=record["schemaHash"],
                    reference=record["secretReference"],
                )
            except SecretCustodyError as exc:
                raise IntegrityError(exc.code) from None
            if status.get("presentSecretKeys") != record["presentSecretKeys"]:
                raise IntegrityError("secret-custody-presence-mismatch")
        return self._project(record, schema, duplicate=None)

    @staticmethod
    def _schema(
        envelope: dict[str, Any], plan_hash: str, *, integrity: bool
    ) -> dict[str, Any]:
        try:
            return configuration_schema(
                envelope, expected_plan_hash=plan_hash
            )
        except ExtensionConfigurationError as exc:
            if integrity:
                raise IntegrityError("configuration-schema-invalid") from None
            raise ValidationRejected(exc.code) from None

    @staticmethod
    def _verify_record(
        loaded: dict[str, Any], schema: dict[str, Any]
    ) -> None:
        record = loaded["configuration"]
        try:
            receipt = validate_stored_configuration(
                loaded["envelope"],
                record["values"],
                record["presentSecretKeys"],
                expected_plan_hash=record["planHash"],
                expected_schema_hash=record["schemaHash"],
            )
        except ExtensionConfigurationError:
            raise IntegrityError("configuration-record-invalid") from None
        for field in (
            "planHash",
            "schemaHash",
            "presentConfigKeys",
            "presentSecretKeys",
            "appliedDefaultKeys",
        ):
            if record[field] != receipt[field]:
                raise IntegrityError("configuration-record-invalid", field)
        if record["schemaHash"] != schema["schemaHash"]:
            raise IntegrityError("configuration-schema-mismatch")

    @staticmethod
    def _project(
        record: dict[str, Any],
        schema: dict[str, Any],
        *,
        duplicate: bool | None,
    ) -> dict[str, Any]:
        result = {
            "schema": "ods.assistant-first.transaction-configuration-view.v1",
            "transactionId": record["transactionId"],
            "planHash": record["planHash"],
            "schemaHash": record["schemaHash"],
            "fields": schema["fields"],
            "configured": True,
            "values": dict(record["values"]),
            "presentConfigKeys": list(record["presentConfigKeys"]),
            "presentSecretKeys": list(record["presentSecretKeys"]),
            "appliedDefaultKeys": list(record["appliedDefaultKeys"]),
        }
        if duplicate is not None:
            result["duplicate"] = duplicate
        return result


__all__ = ["TransactionConfigurationManager"]
