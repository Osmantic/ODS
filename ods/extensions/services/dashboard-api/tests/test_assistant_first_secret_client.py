from __future__ import annotations

import json

import pytest
from assistant_first_secret_client import HostSecretCustodian, SecretCustodyError
from host_agent_client import AgentHTTPError

TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
SCHEMA_HASH = "3" * 64
IDEMPOTENCY_KEY = "4" * 64
REFERENCE = "secret-v1-" + "5" * 48
SENTINEL = "private-do-not-echo"


def status(*, duplicate: bool | None = None) -> dict:
    result = {
        "schema": "ods.assistant-first.secret-status.v1",
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "schemaHash": SCHEMA_HASH,
        "reference": REFERENCE,
        "configured": True,
        "presentSecretKeys": ["API_KEY"],
    }
    if duplicate is not None:
        result["duplicate"] = duplicate
    return result


def test_stage_uses_fixed_post_and_never_projects_secret() -> None:
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return status(duplicate=False)

    result = HostSecretCustodian(request).stage(
        transaction_id=TRANSACTION_ID,
        plan_hash=PLAN_HASH,
        schema_hash=SCHEMA_HASH,
        idempotency_key=IDEMPOTENCY_KEY,
        secret_values={"API_KEY": SENTINEL},
    )
    method, path, kwargs = calls[0]
    assert (method, path, kwargs["timeout"]) == (
        "POST",
        "/v1/assistant-first/secrets/stage",
        10.0,
    )
    assert kwargs["payload"]["secretValues"] == {"API_KEY": SENTINEL}
    assert SENTINEL not in json.dumps(result)


def test_status_uses_fixed_post_and_exact_reference_binding() -> None:
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return status()

    result = HostSecretCustodian(request).status(
        transaction_id=TRANSACTION_ID,
        plan_hash=PLAN_HASH,
        schema_hash=SCHEMA_HASH,
        reference=REFERENCE,
    )
    assert calls[0][0:2] == (
        "POST",
        "/v1/assistant-first/secrets/status",
    )
    assert calls[0][2]["payload"]["reference"] == REFERENCE
    assert result["reference"] == REFERENCE


def test_transport_error_text_cannot_escape_custody_boundary() -> None:
    def request(*_args, **_kwargs):
        raise AgentHTTPError(500, SENTINEL, SENTINEL)

    with pytest.raises(SecretCustodyError) as caught:
        HostSecretCustodian(request).stage(
            transaction_id=TRANSACTION_ID,
            plan_hash=PLAN_HASH,
            schema_hash=SCHEMA_HASH,
            idempotency_key=IDEMPOTENCY_KEY,
            secret_values={"API_KEY": SENTINEL},
        )
    assert caught.value.code == "secret-custody-unavailable"
    assert SENTINEL not in str(caught.value)
    assert SENTINEL not in repr(caught.value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(extra=True),
        lambda value: value.update(planHash="0" * 64),
        lambda value: value.update(reference="wrong"),
        lambda value: value.update(presentSecretKeys=["TOKEN", "API_KEY"]),
        lambda value: value.update(duplicate="false"),
    ],
)
def test_stage_rejects_non_exact_or_misbound_response(mutation) -> None:
    response = status(duplicate=False)
    mutation(response)
    custodian = HostSecretCustodian(lambda *_args, **_kwargs: response)
    with pytest.raises(SecretCustodyError):
        custodian.stage(
            transaction_id=TRANSACTION_ID,
            plan_hash=PLAN_HASH,
            schema_hash=SCHEMA_HASH,
            idempotency_key=IDEMPOTENCY_KEY,
            secret_values={"API_KEY": SENTINEL},
        )


def test_delete_requires_exact_value_safe_response() -> None:
    response = {
        "schema": "ods.assistant-first.secret-status.v1",
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "schemaHash": SCHEMA_HASH,
        "configured": False,
        "deleted": True,
        "presentSecretKeys": [],
    }
    custodian = HostSecretCustodian(lambda *_args, **_kwargs: response)
    assert custodian.delete(
        transaction_id=TRANSACTION_ID,
        plan_hash=PLAN_HASH,
        schema_hash=SCHEMA_HASH,
        reference=REFERENCE,
    )["deleted"] is True

    response["reference"] = REFERENCE
    with pytest.raises(SecretCustodyError):
        custodian.delete(
            transaction_id=TRANSACTION_ID,
            plan_hash=PLAN_HASH,
            schema_hash=SCHEMA_HASH,
            reference=REFERENCE,
        )
