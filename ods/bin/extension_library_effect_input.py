"""Capture exact v2 library bytes for a future host-owned extension effect.

The immutable artifact store persists manifest/Compose, not a full library
payload. This dormant bridge consumes an exact, already-bound applying command
and a batch read back from that store, then snapshots every approved definition
file through no-follow descriptors. A later materializer must consume only the
returned bytes under the same host admission; it may not reopen live paths.
This module does not grant Compose, hook, install, or lifecycle authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from extension_artifact_stage_store import (
    StagedArtifactBatch,
    StagedArtifactFile,
    StagedDefinitionArtifacts,
)
from extension_document_digest import CanonicalDocumentError, canonical_document_sha256
from extension_library_tree_digest import (
    LibraryTreeDigestError,
    LibraryTreeSnapshot,
    snapshot_extension_tree,
)
from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedOperation,
)
from extension_lifecycle_work import LifecycleWorkCommand, LifecycleWorkValidationError


_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class LibraryEffectInputError(LifecycleWorkValidationError):
    """Stable, value-free refusal before any application side effect."""


@dataclass(frozen=True)
class VerifiedLibraryEffectInput:
    transaction_id: str
    plan_hash: str
    service_id: str
    action: str
    payload: LibraryTreeSnapshot


def _deny(code: str) -> None:
    raise LibraryEffectInputError(code) from None


def _planned_definition(
    command: Any,
) -> tuple[PlannedDefinition, tuple[str, ...], str]:
    if type(command) is not LifecycleWorkCommand or len(command.service_ids) != 1:
        _deny("library-effect-command-invalid")
    service_id = command.service_ids[0]
    if command.operation_key != f"apply:{service_id}":
        _deny("library-effect-command-invalid")
    material = command.plan_material
    if (
        type(material) is not LifecyclePlanMaterial
        or material.schema != PLAN_MATERIAL_SCHEMA
        or material.state != "applying"
        or material.transaction_id != command.transaction_id
        or material.plan_hash != command.plan_hash
        or material.attested_approval is not True
        or type(material.operations) is not tuple
        or type(material.definitions) is not tuple
        or len(material.operations) != len(material.definitions)
    ):
        _deny("library-effect-plan-mismatch")
    mutable: list[str] = []
    selected: PlannedDefinition | None = None
    selected_action: str | None = None
    for operation, definition in zip(
        material.operations, material.definitions, strict=True
    ):
        if (
            type(operation) is not PlannedOperation
            or type(definition) is not PlannedDefinition
            or definition.service_id != operation.service_id
        ):
            _deny("library-effect-plan-mismatch")
        if operation.action != "noop":
            mutable.append(operation.service_id)
        if operation.service_id == service_id:
            if selected is not None or operation.action == "noop":
                _deny("library-effect-plan-mismatch")
            if command.payload != {
                "operation": {"serviceId": service_id, "action": operation.action}
            }:
                _deny("library-effect-plan-mismatch")
            selected = definition
            selected_action = operation.action
    if selected is None or selected_action is None or len(mutable) != len(set(mutable)):
        _deny("library-effect-plan-mismatch")
    if (
        selected.manifest_schema_version != "ods.services.v2"
        or selected.service_type != "docker"
        or selected.definition_source != "library"
        or not isinstance(selected.source_tree_sha256, str)
        or _DIGEST_RE.fullmatch(selected.source_tree_sha256) is None
        or not isinstance(selected.definition_sha256, str)
        or _DIGEST_RE.fullmatch(selected.definition_sha256) is None
        or not isinstance(selected.compose_file, str)
        or not isinstance(selected.compose_sha256, str)
        or _DIGEST_RE.fullmatch(selected.compose_sha256) is None
    ):
        _deny("library-effect-definition-unsupported")
    return selected, tuple(mutable), selected_action


def _staged_definition(
    command: LifecycleWorkCommand,
    batch: Any,
    mutable: tuple[str, ...],
) -> StagedDefinitionArtifacts:
    if (
        type(batch) is not StagedArtifactBatch
        or batch.transaction_id != command.transaction_id
        or batch.plan_hash != command.plan_hash
        or batch.service_ids != mutable
        or type(batch.definitions) is not tuple
        or len(batch.definitions) != len(mutable)
        or any(
            type(item) is not StagedDefinitionArtifacts for item in batch.definitions
        )
        or tuple(item.service_id for item in batch.definitions) != mutable
        or not isinstance(batch.bundle_sha256, str)
        or _HEX_RE.fullmatch(batch.bundle_sha256) is None
        or type(batch.duplicate) is not bool
    ):
        _deny("library-effect-stage-mismatch")
    result = batch.definitions[mutable.index(command.service_ids[0])]
    if (
        type(result) is not StagedDefinitionArtifacts
        or result.definition_source != "library"
        or type(result.manifest) is not StagedArtifactFile
        or type(result.compose) is not StagedArtifactFile
    ):
        _deny("library-effect-stage-mismatch")
    return result


def verify_plan_bound_library_payload(
    command: LifecycleWorkCommand,
    staged_batch: StagedArtifactBatch,
    library_root: Path,
) -> VerifiedLibraryEffectInput:
    """Return all approved bytes, refusing stale source or stage bindings.

    The caller must first acquire host admission and read ``staged_batch`` from
    the fixed immutable store. This function only captures bytes; it neither
    starts an application nor certifies health or recoverability.
    """

    definition, mutable, action = _planned_definition(command)
    staged = _staged_definition(command, staged_batch, mutable)
    if (
        not isinstance(library_root, Path)
        or not library_root.is_absolute()
        or ".." in library_root.parts
    ):
        _deny("library-effect-root-invalid")
    try:
        payload = snapshot_extension_tree(library_root / definition.service_id)
    except LibraryTreeDigestError:
        _deny("library-effect-source-invalid")
    if payload.digest != definition.source_tree_sha256:
        _deny("library-effect-source-mismatch")
    files = {item.relative_path: item.content for item in payload.files}
    if len(files) != len(payload.files):
        _deny("library-effect-source-invalid")
    manifest = files.get("manifest.yaml")
    compose = files.get(definition.compose_file)
    if (
        type(manifest) is not bytes
        or type(compose) is not bytes
        or staged.manifest.relative_path != "manifest.yaml"
        or staged.compose.relative_path != definition.compose_file
        or staged.manifest.content != manifest
        or staged.compose.content != compose
    ):
        _deny("library-effect-stage-mismatch")
    try:
        if (
            canonical_document_sha256(manifest) != definition.definition_sha256
            or canonical_document_sha256(compose) != definition.compose_sha256
        ):
            _deny("library-effect-stage-mismatch")
    except CanonicalDocumentError:
        _deny("library-effect-stage-mismatch")
    return VerifiedLibraryEffectInput(
        transaction_id=command.transaction_id,
        plan_hash=command.plan_hash,
        service_id=definition.service_id,
        action=action,
        payload=payload,
    )


__all__ = [
    "LibraryEffectInputError",
    "VerifiedLibraryEffectInput",
    "verify_plan_bound_library_payload",
]
