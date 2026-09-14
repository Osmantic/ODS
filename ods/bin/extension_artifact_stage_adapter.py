"""Bind plan-selected verification to one immutable artifact stage write.

This dormant adapter is the only bridge between the read-only host artifact
verifier and the immutable stage store.  It validates the complete command
before opening a definition, verifies every selected definition before the
single publication call, and returns only the staged bundle digest expected by
the lifecycle-work dispatcher contract.  It does not create roots, discover
definitions, evaluate Compose, or operate services and containers.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from extension_artifact_stage_store import (
    ArtifactStageError,
    ArtifactStageStore,
    StagedArtifactBatch,
    StagedDefinitionArtifacts,
    select_stage_definitions,
)
from extension_artifact_verifier import (
    HostArtifactError,
    HostArtifactRoots,
    VerifiedDefinitionArtifacts,
    verify_planned_definition,
)
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkExecutionError,
    LifecycleWorkValidationError,
)


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

class ArtifactStageAdapterError(LifecycleWorkExecutionError):
    """Stable, value-free failure after command binding was accepted."""


def _execution_error(code: str, cause: BaseException | None = None) -> None:
    if cause is None:
        raise ArtifactStageAdapterError(code) from None
    raise ArtifactStageAdapterError(code) from cause


def _stage_method(store: Any) -> Callable[..., Any]:
    if type(store) is not ArtifactStageStore:
        _execution_error("lifecycle-work-artifact-stage-adapter-invalid")
    method = getattr(store, "stage", None)
    if not callable(method):
        _execution_error("lifecycle-work-artifact-stage-adapter-invalid")
    return method


def validate_staged_batch(
    batch: Any,
    command: LifecycleWorkCommand,
) -> StagedArtifactBatch:
    if type(batch) is not StagedArtifactBatch:
        _execution_error("lifecycle-work-artifact-stage-evidence-mismatch")
    definitions = batch.definitions
    if (
        batch.transaction_id != command.transaction_id
        or batch.plan_hash != command.plan_hash
        or batch.service_ids != command.service_ids
        or not isinstance(definitions, tuple)
        or len(definitions) != len(command.service_ids)
        or any(
            type(definition) is not StagedDefinitionArtifacts
            for definition in definitions
        )
        or tuple(definition.service_id for definition in definitions)
        != command.service_ids
        or not isinstance(batch.bundle_sha256, str)
        or _HASH_RE.fullmatch(batch.bundle_sha256) is None
        or type(batch.duplicate) is not bool
    ):
        _execution_error("lifecycle-work-artifact-stage-evidence-mismatch")
    return batch


class ArtifactStageAdapter:
    """Callable dispatcher for only the plan-bound ``stage`` operation."""

    def __init__(
        self,
        roots: HostArtifactRoots,
        store: ArtifactStageStore,
    ) -> None:
        if type(roots) is not HostArtifactRoots:
            _execution_error("lifecycle-work-artifact-stage-adapter-invalid")
        _stage_method(store)
        self._roots = roots
        self._store = store

    def __call__(self, command: LifecycleWorkCommand) -> str:
        """Verify the full batch, publish it once, and return its exact hash."""

        try:
            definitions = select_stage_definitions(command)
        except ArtifactStageError as exc:
            raise LifecycleWorkValidationError(
                "lifecycle-work-plan-mismatch"
            ) from exc

        verified: list[VerifiedDefinitionArtifacts] = []
        for definition in definitions:
            try:
                result = verify_planned_definition(definition, self._roots)
            except HostArtifactError as exc:
                _execution_error(
                    "lifecycle-work-artifact-verification-failed", exc
                )
            except Exception as exc:
                _execution_error(
                    "lifecycle-work-artifact-verification-failed", exc
                )
            if type(result) is not VerifiedDefinitionArtifacts:
                _execution_error("lifecycle-work-artifact-verification-failed")
            verified.append(result)

        try:
            batch = self._store.stage(command, tuple(verified))
        except ArtifactStageError as exc:
            _execution_error("lifecycle-work-artifact-stage-failed", exc)
        except Exception as exc:
            _execution_error("lifecycle-work-artifact-stage-failed", exc)
        return validate_staged_batch(batch, command).bundle_sha256


__all__ = [
    "ArtifactStageAdapter",
    "ArtifactStageAdapterError",
    "validate_staged_batch",
]
