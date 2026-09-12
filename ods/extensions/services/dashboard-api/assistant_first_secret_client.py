"""Value-safe Dashboard client for host-owned Assistant First secret custody."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from host_agent_client import AgentClientError, request_json

STAGE_REQUEST_SCHEMA = "ods.assistant-first.secret-stage-request.v1"
STATUS_REQUEST_SCHEMA = "ods.assistant-first.secret-status-request.v1"
DELETE_REQUEST_SCHEMA = "ods.assistant-first.secret-delete-request.v1"
STATUS_SCHEMA = "ods.assistant-first.secret-status.v1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TRANSACTION_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_REFERENCE_RE = re.compile(r"^secret-v1-[0-9a-f]{48}$")
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_DELETE_RESPONSE_KEYS = frozenset(
    {
        "configured",
        "deleted",
        "planHash",
        "presentSecretKeys",
        "schema",
        "schemaHash",
        "transactionId",
    }
)


class SecretCustodyError(RuntimeError):
    """Stable custody failure whose text never contains submitted values."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def __repr__(self) -> str:
        return f"SecretCustodyError({self.code!r})"


def _fail(code: str) -> None:
    raise SecretCustodyError(code)


def _binding(transaction_id: str, plan_hash: str, schema_hash: str) -> None:
    if not isinstance(transaction_id, str) or not _TRANSACTION_RE.fullmatch(
        transaction_id
    ):
        _fail("secret-custody-invalid-binding")
    if not isinstance(plan_hash, str) or not _HASH_RE.fullmatch(plan_hash):
        _fail("secret-custody-invalid-binding")
    if not isinstance(schema_hash, str) or not _HASH_RE.fullmatch(schema_hash):
        _fail("secret-custody-invalid-binding")


def _status(
    response: Any,
    *,
    transaction_id: str,
    plan_hash: str,
    schema_hash: str,
    reference: str | None,
    stage: bool,
) -> dict[str, Any]:
    required = {
        "schema",
        "transactionId",
        "planHash",
        "schemaHash",
        "configured",
        "presentSecretKeys",
    }
    allowed = required | {"reference", "duplicate"} if stage else required | {
        "reference"
    }
    if not isinstance(response, dict) or not required <= set(response) <= allowed:
        _fail("secret-custody-invalid-response")
    if (
        response["schema"] != STATUS_SCHEMA
        or response["transactionId"] != transaction_id
        or response["planHash"] != plan_hash
        or response["schemaHash"] != schema_hash
        or response["configured"] is not True
    ):
        _fail("secret-custody-binding-mismatch")
    actual_reference = response.get("reference")
    if not isinstance(actual_reference, str) or not _REFERENCE_RE.fullmatch(
        actual_reference
    ):
        _fail("secret-custody-invalid-response")
    if reference is not None and actual_reference != reference:
        _fail("secret-custody-binding-mismatch")
    keys = response["presentSecretKeys"]
    if (
        not isinstance(keys, list)
        or keys != sorted(set(keys))
        or any(not isinstance(key, str) or not _KEY_RE.fullmatch(key) for key in keys)
    ):
        _fail("secret-custody-invalid-response")
    if stage and type(response.get("duplicate")) is not bool:
        _fail("secret-custody-invalid-response")
    return dict(response)


class HostSecretCustodian:
    """Stage and verify secrets through fixed POST-only host-agent routes."""

    def __init__(self, requester: Callable[..., dict[str, Any]] = request_json) -> None:
        self._request = requester

    def stage(
        self,
        *,
        transaction_id: str,
        plan_hash: str,
        schema_hash: str,
        idempotency_key: str,
        secret_values: Mapping[str, Any],
    ) -> dict[str, Any]:
        _binding(transaction_id, plan_hash, schema_hash)
        payload = {
            "schema": STAGE_REQUEST_SCHEMA,
            "transactionId": transaction_id,
            "planHash": plan_hash,
            "schemaHash": schema_hash,
            "idempotencyKey": idempotency_key,
            "secretValues": dict(secret_values),
        }
        try:
            response = self._request(
                "POST",
                "/v1/assistant-first/secrets/stage",
                payload=payload,
                timeout=10.0,
            )
        except AgentClientError:
            _fail("secret-custody-unavailable")
        return _status(
            response,
            transaction_id=transaction_id,
            plan_hash=plan_hash,
            schema_hash=schema_hash,
            reference=None,
            stage=True,
        )

    def status(
        self,
        *,
        transaction_id: str,
        plan_hash: str,
        schema_hash: str,
        reference: str,
    ) -> dict[str, Any]:
        _binding(transaction_id, plan_hash, schema_hash)
        payload = {
            "schema": STATUS_REQUEST_SCHEMA,
            "transactionId": transaction_id,
            "planHash": plan_hash,
            "schemaHash": schema_hash,
            "reference": reference,
        }
        try:
            response = self._request(
                "POST",
                "/v1/assistant-first/secrets/status",
                payload=payload,
                timeout=5.0,
            )
        except AgentClientError:
            _fail("secret-custody-unavailable")
        return _status(
            response,
            transaction_id=transaction_id,
            plan_hash=plan_hash,
            schema_hash=schema_hash,
            reference=reference,
            stage=False,
        )

    def delete(
        self,
        *,
        transaction_id: str,
        plan_hash: str,
        schema_hash: str,
        reference: str,
    ) -> dict[str, Any]:
        _binding(transaction_id, plan_hash, schema_hash)
        payload = {
            "schema": DELETE_REQUEST_SCHEMA,
            "transactionId": transaction_id,
            "planHash": plan_hash,
            "schemaHash": schema_hash,
            "reference": reference,
        }
        try:
            response = self._request(
                "POST",
                "/v1/assistant-first/secrets/delete",
                payload=payload,
                timeout=5.0,
            )
        except AgentClientError:
            _fail("secret-custody-unavailable")
        if (
            not isinstance(response, dict)
            or set(response) != _DELETE_RESPONSE_KEYS
            or response["schema"] != STATUS_SCHEMA
            or response["transactionId"] != transaction_id
            or response["planHash"] != plan_hash
            or response["schemaHash"] != schema_hash
            or response["configured"] is not False
            or type(response["deleted"]) is not bool
            or response["presentSecretKeys"] != []
        ):
            _fail("secret-custody-invalid-response")
        return dict(response)


__all__ = ["HostSecretCustodian", "SecretCustodyError"]
