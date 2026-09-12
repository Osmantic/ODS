"""Fail-closed HTTP boundary for Assistant First extension transactions."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, TypeVar

from assistant_first_planner import PlanningError
from extension_transaction_runtime import TransactionRuntime
from extension_transactions import (
    ApprovalError,
    IdempotencyConflict,
    IntegrityError,
    TransactionError,
    TransitionError,
    ValidationRejected,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from plan_provenance import authorize_plan
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from security import verify_api_key

from routers.auth import require_owner_approval_session

router = APIRouter(prefix="/api/extensions/transactions", tags=["extensions"])
MAX_REQUEST_BYTES = 64 * 1024
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})
_Model = TypeVar("_Model", bound=BaseModel)


class CreateTransactionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent: dict[str, Any]
    idempotencyKey: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class ExactHashRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    planHash: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class ConfigurationSubmissionRequest(ExactHashRequest):
    model_config = ConfigDict(extra="forbid", strict=True)

    schemaHash: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    idempotencyKey: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    values: dict[str, Any]
    secretValues: dict[str, Any]


def _feature_enabled() -> bool:
    return (
        os.environ.get("ODS_ASSISTANT_TRANSACTIONS_ENABLED", "").strip().lower()
        in _ENABLED_VALUES
    )


def get_transaction_runtime(request: Request) -> TransactionRuntime:
    """Resolve one explicitly installed runtime after the feature gate."""
    if not _feature_enabled():
        raise HTTPException(
            status_code=404,
            detail="Not found",
            headers={"Cache-Control": "no-store"},
        )
    runtime = getattr(request.app.state, "extension_transaction_runtime", None)
    if not isinstance(runtime, TransactionRuntime):
        raise HTTPException(
            status_code=503,
            detail="Extension transaction runtime is unavailable",
            headers={"Cache-Control": "no-store"},
        )
    return runtime


def _configuration_manager(runtime: TransactionRuntime) -> Any:
    manager = runtime.configuration
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="Extension transaction configuration is unavailable",
            headers={"Cache-Control": "no-store"},
        )
    return manager


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate-key")
        value[key] = item
    return value


def _reject_number(_value: str) -> None:
    raise ValueError("floating-point-values-are-not-accepted")


async def _request_model(request: Request, model_type: type[_Model]) -> _Model:
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_REQUEST_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Transaction request is too large",
                headers={"Cache-Control": "no-store"},
            )
    try:
        decoded = json.loads(
            bytes(payload).decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        return model_type.model_validate(decoded)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        raise HTTPException(
            status_code=422,
            detail="Invalid transaction request",
            headers={"Cache-Control": "no-store"},
        ) from None


def _error_response(exc: Exception) -> JSONResponse:
    code = getattr(exc, "code", "transaction-unavailable")
    if not isinstance(code, str):
        code = "transaction-unavailable"
    if code == "not-found":
        status_code = 404
    elif isinstance(exc, ValidationRejected):
        status_code = 422
    elif isinstance(exc, (IdempotencyConflict, ApprovalError, TransitionError)):
        status_code = 409
    elif isinstance(exc, IntegrityError):
        status_code = 503
    else:
        status_code = 503
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code}},
        headers={"Cache-Control": "no-store"},
    )


def _planning_error_response(exc: PlanningError) -> JSONResponse:
    provider_errors = {
        "invalid-catalog-provider",
        "invalid-observed-state-provider",
        "invalid-policy-provider",
        "catalog-revision-mismatch",
    }
    if exc.code in provider_errors:
        status_code = 503
    elif exc.code.startswith(
        (
            "invalid-",
            "duplicate-",
            "extra-",
            "unknown-",
            "unused-",
            "config-secret-",
        )
    ):
        status_code = 422
    else:
        status_code = 409
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": exc.code}},
        headers={"Cache-Control": "no-store"},
    )


@router.post("")
async def create_transaction(
    request: Request,
    runtime: TransactionRuntime = Depends(get_transaction_runtime),
    _api_key: str = Depends(verify_api_key),
) -> JSONResponse:
    """Build a server-authoritative plan and persist it awaiting approval."""
    model = await _request_model(request, CreateTransactionRequest)
    try:
        envelope = authorize_plan(
            model.intent,
            catalog=runtime.catalog,
            observed_state=runtime.observed_state,
            policy=runtime.policy,
        )
    except PlanningError as exc:
        return _planning_error_response(exc)
    timestamp = runtime.clock()
    try:
        descriptor = runtime.store.create(
            envelope,
            "assistant-manager",
            model.idempotencyKey,
            timestamp,
            timestamp,
        )
    except TransactionError as exc:
        return _error_response(exc)
    return JSONResponse(
        status_code=200 if descriptor.get("duplicate") is True else 201,
        content={
            "schema": "ods.assistant-first.transaction-proposal.v1",
            **descriptor,
            "envelope": envelope,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{transaction_id}/approval")
async def approve_transaction(
    transaction_id: str,
    request: Request,
    approved_by: str = Depends(require_owner_approval_session),
    runtime: TransactionRuntime = Depends(get_transaction_runtime),
) -> JSONResponse:
    """Bind one owner browser session to one exact stored plan hash."""
    model = await _request_model(request, ExactHashRequest)
    try:
        await asyncio.to_thread(
            _configuration_manager(runtime).require_ready,
            transaction_id,
            model.planHash,
        )
        timestamp = runtime.clock()
        result = runtime.store.approve_exact(
            transaction_id,
            model.planHash,
            approved_by,
            timestamp,
            timestamp,
        )
    except TransactionError as exc:
        return _error_response(exc)
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


@router.get("/{transaction_id}/configuration")
async def transaction_configuration(
    transaction_id: str,
    runtime: TransactionRuntime = Depends(get_transaction_runtime),
    _api_key: str = Depends(verify_api_key),
) -> JSONResponse:
    """Return the stored-plan-derived schema and value-safe configuration."""
    try:
        result = await asyncio.to_thread(
            _configuration_manager(runtime).view, transaction_id
        )
    except TransactionError as exc:
        return _error_response(exc)
    except Exception:
        return _error_response(IntegrityError("configuration-unavailable"))
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


@router.post("/{transaction_id}/configuration")
async def configure_transaction(
    transaction_id: str,
    request: Request,
    runtime: TransactionRuntime = Depends(get_transaction_runtime),
    _api_key: str = Depends(verify_api_key),
) -> JSONResponse:
    """Validate configuration and send secret values directly to host custody."""
    model = await _request_model(request, ConfigurationSubmissionRequest)
    try:
        result = await asyncio.to_thread(
            _configuration_manager(runtime).submit,
            transaction_id,
            plan_hash=model.planHash,
            schema_hash=model.schemaHash,
            idempotency_key=model.idempotencyKey,
            values=model.values,
            secret_values=model.secretValues,
        )
    except TransactionError as exc:
        return _error_response(exc)
    except Exception:
        return _error_response(IntegrityError("configuration-unavailable"))
    return JSONResponse(
        status_code=200 if result.get("duplicate") is True else 201,
        content=result,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{transaction_id}")
async def transaction_status(
    transaction_id: str,
    runtime: TransactionRuntime = Depends(get_transaction_runtime),
    _api_key: str = Depends(verify_api_key),
) -> JSONResponse:
    """Return reviewable plan and durable progress without approval secrets."""
    try:
        loaded = runtime.store.read(transaction_id)
    except TransactionError as exc:
        return _error_response(exc)
    envelope = loaded["envelope"]
    approval = loaded["approval"]
    safe_approval = {
        "approved": approval is not None,
        "approvedAt": approval.get("approvedAt") if approval else None,
        "approvedBy": approval.get("approvedBy") if approval else None,
    }
    return JSONResponse(
        content={
            "schema": "ods.assistant-first.transaction-status.v1",
            "transactionId": loaded["transactionId"],
            "planHash": envelope["planHash"],
            "catalogRevision": envelope["catalogRevision"],
            "observedStateRevision": envelope["observedStateRevision"],
            "policyRevision": envelope["policyRevision"],
            "state": loaded["state"],
            "sequence": loaded["sequence"],
            "plan": envelope["plan"],
            "journal": loaded["journal"],
            "approval": safe_approval,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{transaction_id}/execute")
async def execute_transaction(
    transaction_id: str,
    request: Request,
    runtime: TransactionRuntime = Depends(get_transaction_runtime),
    _api_key: str = Depends(verify_api_key),
) -> JSONResponse:
    """Execute only an exact stored and approved transaction, synchronously."""
    model = await _request_model(request, ExactHashRequest)
    if runtime.executor is None:
        raise HTTPException(
            status_code=503,
            detail="Extension transaction execution is unavailable",
            headers={"Cache-Control": "no-store"},
        )
    try:
        await asyncio.to_thread(
            _configuration_manager(runtime).require_ready,
            transaction_id,
            model.planHash,
        )
        result = runtime.executor.execute(transaction_id, model.planHash)
    except TransactionError as exc:
        return _error_response(exc)
    return JSONResponse(
        content={
            "transactionId": result.transaction_id,
            "planHash": model.planHash,
            "finalState": result.final_state,
            "sequence": result.sequence,
            "appliedServices": result.applied_services,
            "error": result.error,
        },
        headers={"Cache-Control": "no-store"},
    )
