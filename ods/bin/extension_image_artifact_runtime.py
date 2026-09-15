"""Pull and verify plan-bound Assistant First image artifacts.

The SearXNG first-boot canary retains its exact frozen allowlist. Optional
library images are accepted only from a durable v2-attested owner-approved
plan; every target must name an immutable ``reference@digest``. This module
prepares images only. It cannot stage, configure, apply, or start Compose.

The paired started-receipt observer performs only ``docker image inspect``.
That makes a lost response after a successful pull recoverable without a
second network operation.  Construction itself performs no process, network,
filesystem, container, or Compose effect.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedImage,
    PlannedOperation,
)
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkError,
    LifecycleWorkExecutionError,
    LifecycleWorkStartedObservation,
    LifecycleWorkValidationError,
)


CANARY_SERVICE_ID = "searxng"
CANARY_MANIFEST_SCHEMA = "ods.services.v2"
CANARY_VERSION = "2026.3.8"
CANARY_DATA_SCHEMA_VERSION = "1"
CANARY_DEFINITION_SHA256 = (
    "sha256:7b2bdcfceb5871a0f107b8fe1c2b44441e10d78367cdc1f0eb0d9a6ac15ed384"
)
CANARY_COMPOSE_SHA256 = (
    "sha256:aebfd89f6e5ca09f515e8db5be200b772b9c597a5d6cee5352910a39651971b4"
)
CANARY_IMAGE_REFERENCE = "searxng/searxng:2026.3.8-a563127a2"
CANARY_IMAGE_DIGEST = (
    "sha256:754a07a64e926a1fc0a8a30cd7a07d08278188f0ef6143e38ad0b22ea8599c55"
)
CANARY_IMAGE_DOWNLOAD_BYTES = 97_736_439

_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_ACTIONS = frozenset({"install", "enable", "repair", "update"})
_MAX_ATTESTED_IMAGES = 64
_Runner = Callable[..., Any]
_PlanLoader = Callable[[LifecycleWorkCommand], LifecycleWorkCommand]


class ImageArtifactRuntimeError(LifecycleWorkExecutionError):
    """Stable, value-free failure after an image command was accepted."""


def _validation_error(code: str) -> None:
    raise LifecycleWorkValidationError(code) from None


def _execution_error(code: str, cause: BaseException | None = None) -> None:
    if cause is None:
        raise ImageArtifactRuntimeError(code) from None
    raise ImageArtifactRuntimeError(code) from cause


@dataclass(frozen=True)
class _ImageTarget:
    reference: str
    digest: str
    service_id: str | None = None

    @property
    def immutable_reference(self) -> str:
        return f"{self.reference}@{self.digest}"


def _validate_bound_command(command: Any) -> _ImageTarget:
    """Re-prove the exact one-service canary before any subprocess call."""

    if type(command) is not LifecycleWorkCommand:
        _validation_error("lifecycle-work-command-invalid")
    if command.operation_key != "download-and-verify":
        _validation_error("lifecycle-work-operation-mismatch")
    if command.service_ids != (CANARY_SERVICE_ID,):
        _validation_error("lifecycle-work-image-canary-denied")

    material = command.plan_material
    if (
        type(material) is not LifecyclePlanMaterial
        or material.schema != PLAN_MATERIAL_SCHEMA
        or material.transaction_id != command.transaction_id
        or material.plan_hash != command.plan_hash
        or material.state != "downloading"
        or type(material.operations) is not tuple
        or type(material.definitions) is not tuple
        or len(material.operations) != 1
        or len(material.definitions) != 1
    ):
        _validation_error("lifecycle-work-plan-mismatch")

    operation = material.operations[0]
    definition = material.definitions[0]
    if (
        type(operation) is not PlannedOperation
        or type(definition) is not PlannedDefinition
        or operation.service_id != CANARY_SERVICE_ID
        or definition.service_id != CANARY_SERVICE_ID
        or operation.action not in _ACTIONS
        or command.payload
        != {
            "operations": [{"serviceId": CANARY_SERVICE_ID, "action": operation.action}]
        }
    ):
        _validation_error("lifecycle-work-plan-mismatch")

    if (
        definition.service_type != "docker"
        or definition.manifest_schema_version != CANARY_MANIFEST_SCHEMA
        or definition.version != CANARY_VERSION
        or definition.data_schema_version != CANARY_DATA_SCHEMA_VERSION
        or definition.definition_source != "builtin"
        or definition.definition_sha256 != CANARY_DEFINITION_SHA256
        or definition.compose_sha256 != CANARY_COMPOSE_SHA256
        or definition.compose_file != "compose.yaml"
        or type(definition.images) is not tuple
        or len(definition.images) != 1
        or type(definition.builds) is not tuple
        or definition.builds
    ):
        _validation_error("lifecycle-work-image-canary-denied")

    image = definition.images[0]
    if (
        type(image) is not PlannedImage
        or image.reference != CANARY_IMAGE_REFERENCE
        or image.digest != CANARY_IMAGE_DIGEST
        or isinstance(image.download_bytes, bool)
        or not isinstance(image.download_bytes, int)
        or image.download_bytes != CANARY_IMAGE_DOWNLOAD_BYTES
    ):
        _validation_error("lifecycle-work-image-canary-denied")
    return _ImageTarget(image.reference, image.digest)


def _validate_attested_library_targets(command: Any) -> tuple[_ImageTarget, ...]:
    """Accept only immutable images in the exact v2-approved library mutation."""

    if type(command) is not LifecycleWorkCommand:
        _validation_error("lifecycle-work-command-invalid")
    if command.operation_key != "download-and-verify":
        _validation_error("lifecycle-work-operation-mismatch")
    material = command.plan_material
    if (
        type(material) is not LifecyclePlanMaterial
        or material.schema != PLAN_MATERIAL_SCHEMA
        or material.transaction_id != command.transaction_id
        or material.plan_hash != command.plan_hash
        or material.state != "downloading"
        or material.attested_approval is not True
        or type(material.operations) is not tuple
        or type(material.definitions) is not tuple
        or len(material.operations) != len(material.definitions)
        or not material.operations
    ):
        _validation_error("lifecycle-work-plan-mismatch")

    mutable = tuple(
        operation for operation in material.operations
        if type(operation) is PlannedOperation and operation.action != "noop"
    )
    if (
        any(type(operation) is not PlannedOperation for operation in material.operations)
        or any(operation.action not in _ACTIONS for operation in mutable)
        or command.service_ids != tuple(operation.service_id for operation in mutable)
        or command.payload != {
            "operations": [
                {"serviceId": operation.service_id, "action": operation.action}
                for operation in mutable
            ]
        }
    ):
        _validation_error("lifecycle-work-plan-mismatch")

    targets: list[_ImageTarget] = []
    for operation, definition in zip(material.operations, material.definitions):
        if (
            type(operation) is not PlannedOperation
            or type(definition) is not PlannedDefinition
            or definition.service_id != operation.service_id
        ):
            _validation_error("lifecycle-work-plan-mismatch")
        if operation.action == "noop":
            continue
        if (
            definition.service_type != "docker"
            or definition.manifest_schema_version != "ods.services.v2"
            or definition.definition_source != "library"
            or not isinstance(definition.definition_sha256, str)
            or _IMAGE_ID_RE.fullmatch(definition.definition_sha256) is None
            or not isinstance(definition.compose_sha256, str)
            or _IMAGE_ID_RE.fullmatch(definition.compose_sha256) is None
            or not isinstance(definition.compose_file, str)
            or not definition.compose_file
            or type(definition.images) is not tuple
            or not definition.images
            or type(definition.builds) is not tuple
            or definition.builds
        ):
            _validation_error("lifecycle-work-image-attestation-required")
        for image in definition.images:
            if (
                type(image) is not PlannedImage
                or not isinstance(image.reference, str)
                or _IMAGE_REFERENCE_RE.fullmatch(image.reference) is None
                or not isinstance(image.digest, str)
                or _IMAGE_ID_RE.fullmatch(image.digest) is None
                or type(image.download_bytes) is not int
                or image.download_bytes < 0
            ):
                _validation_error("lifecycle-work-image-attestation-required")
            targets.append(_ImageTarget(image.reference, image.digest, definition.service_id))
            if len(targets) > _MAX_ATTESTED_IMAGES:
                _validation_error("lifecycle-work-image-attestation-required")
    if not targets:
        _validation_error("lifecycle-work-image-attestation-required")
    return tuple(targets)


def _validated_targets(command: Any) -> tuple[tuple[_ImageTarget, ...], bool]:
    if type(command) is not LifecycleWorkCommand:
        _validation_error("lifecycle-work-command-invalid")
    if command.service_ids == (CANARY_SERVICE_ID,):
        return (_validate_bound_command(command),), True
    return _validate_attested_library_targets(command), False


def _run(
    runner: _Runner,
    argv: list[str],
    *,
    timeout: float,
    missing_ok: bool = False,
) -> Any | None:
    try:
        completed = runner(
            argv,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _execution_error("lifecycle-work-image-command-timeout", exc)
    except (OSError, subprocess.SubprocessError) as exc:
        _execution_error("lifecycle-work-image-command-failed", exc)
    except Exception as exc:  # noqa: BLE001 - map injected/runtime failures safely
        _execution_error("lifecycle-work-image-command-failed", exc)

    return_code = getattr(completed, "returncode", None)
    if isinstance(return_code, bool) or not isinstance(return_code, int):
        _execution_error("lifecycle-work-image-command-invalid")
    if return_code != 0:
        if missing_ok:
            return None
        _execution_error("lifecycle-work-image-command-failed")
    return completed


def _inspect_image(
    runner: _Runner,
    target: _ImageTarget,
    *,
    timeout: float,
    missing_ok: bool,
) -> str | None:
    completed = _run(
        runner,
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            target.immutable_reference,
        ],
        timeout=timeout,
        missing_ok=missing_ok,
    )
    if completed is None:
        return None
    output = getattr(completed, "stdout", None)
    if not isinstance(output, str):
        _execution_error("lifecycle-work-image-inspect-invalid")
    image_id = output.strip()
    if _IMAGE_ID_RE.fullmatch(image_id) is None:
        _execution_error("lifecycle-work-image-inspect-invalid")
    return image_id


def _evidence_hash(
    command: LifecycleWorkCommand, target: _ImageTarget, image_id: str
) -> str:
    payload = {
        "schema": "ods.extension-image-artifact-evidence.v1",
        "transactionId": command.transaction_id,
        "planHash": command.plan_hash,
        "operationKey": command.operation_key,
        "serviceIds": list(command.service_ids),
        "image": target.immutable_reference,
        "imageId": image_id,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _attested_evidence_hash(
    command: LifecycleWorkCommand,
    inspected: tuple[tuple[_ImageTarget, str], ...],
) -> str:
    payload = {
        "schema": "ods.extension-image-artifact-evidence.v2",
        "transactionId": command.transaction_id,
        "planHash": command.plan_hash,
        "operationKey": command.operation_key,
        "requestHash": command.request_hash,
        "serviceIds": list(command.service_ids),
        "images": [
            {
                "serviceId": target.service_id,
                "image": target.immutable_reference,
                "imageId": image_id,
            }
            for target, image_id in inspected
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _execution_error("lifecycle-work-image-command-timeout")
    return remaining


def _validate_loaded_command(
    original: LifecycleWorkCommand, loaded: Any
) -> LifecycleWorkCommand:
    if (
        type(loaded) is not LifecycleWorkCommand
        or loaded.plan_material is None
        or any(
            getattr(loaded, field) != getattr(original, field)
            for field in (
                "transaction_id",
                "plan_hash",
                "operation_key",
                "request_hash",
                "service_ids",
                "payload",
                "timeout_seconds",
            )
        )
    ):
        _validation_error("lifecycle-work-plan-mismatch")
    return loaded


class ImageArtifactDispatcher:
    """Prepare exact approved immutable images without applying Compose."""

    def __init__(self, runner: _Runner = subprocess.run) -> None:
        if not callable(runner):
            _execution_error("lifecycle-work-image-runtime-invalid")
        self._runner = runner

    def __call__(self, command: LifecycleWorkCommand) -> str:
        targets, canary = _validated_targets(command)
        timeout = float(command.timeout_seconds)
        if canary:
            target = targets[0]
            _run(
                self._runner,
                ["docker", "image", "pull", "--quiet", target.immutable_reference],
                timeout=timeout,
            )
            image_id = _inspect_image(
                self._runner,
                target,
                timeout=min(timeout, 30.0),
                missing_ok=False,
            )
            assert isinstance(image_id, str)
            return _evidence_hash(command, target, image_id)

        deadline = time.monotonic() + timeout
        inspected: list[tuple[_ImageTarget, str]] = []
        observed_ids: dict[str, str] = {}
        for target in targets:
            image_id = observed_ids.get(target.immutable_reference)
            if image_id is None:
                _run(
                    self._runner,
                    ["docker", "image", "pull", "--quiet", target.immutable_reference],
                    timeout=_remaining(deadline),
                )
                image_id = _inspect_image(
                    self._runner,
                    target,
                    timeout=min(_remaining(deadline), 30.0),
                    missing_ok=False,
                )
                assert isinstance(image_id, str)
                observed_ids[target.immutable_reference] = image_id
            assert isinstance(image_id, str)
            inspected.append((target, image_id))
        return _attested_evidence_hash(command, tuple(inspected))


class ImageArtifactStartedObserver:
    """Recover a started pull from the exact image already present locally."""

    def __init__(
        self,
        plan_loader: _PlanLoader,
        runner: _Runner = subprocess.run,
    ) -> None:
        if not callable(plan_loader) or not callable(runner):
            _execution_error("lifecycle-work-image-runtime-invalid")
        self._plan_loader = plan_loader
        self._runner = runner

    def __call__(
        self, command: LifecycleWorkCommand
    ) -> LifecycleWorkStartedObservation:
        if type(command) is not LifecycleWorkCommand:
            _validation_error("lifecycle-work-command-invalid")
        try:
            loaded = self._plan_loader(command)
        except LifecycleWorkError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep raw plan errors private
            _execution_error("lifecycle-work-image-observation-failed", exc)
        bound = _validate_loaded_command(command, loaded)
        targets, canary = _validated_targets(bound)
        if canary:
            target = targets[0]
            image_id = _inspect_image(
                self._runner,
                target,
                timeout=min(float(bound.timeout_seconds), 30.0),
                missing_ok=True,
            )
            if image_id is None:
                return LifecycleWorkStartedObservation(state="missing")
            return LifecycleWorkStartedObservation(
                state="completed",
                evidence_hash=_evidence_hash(bound, target, image_id),
            )

        # Local replay inspection is bounded independently of the longer
        # network download budget; an observer error terminalizes this exact
        # attested request at the host, instead of restarting endless replays.
        deadline = time.monotonic() + min(float(bound.timeout_seconds), 30.0)
        inspected: list[tuple[_ImageTarget, str]] = []
        observed_ids: dict[str, str] = {}
        for target in targets:
            image_id = observed_ids.get(target.immutable_reference)
            if image_id is None:
                image_id = _inspect_image(
                    self._runner,
                    target,
                    timeout=min(_remaining(deadline), 30.0),
                    missing_ok=True,
                )
            if image_id is None:
                return LifecycleWorkStartedObservation(state="missing")
            observed_ids[target.immutable_reference] = image_id
            inspected.append((target, image_id))
        return LifecycleWorkStartedObservation(
            state="completed",
            evidence_hash=_attested_evidence_hash(bound, tuple(inspected)),
        )


@dataclass(frozen=True)
class ImageArtifactRuntime:
    dispatcher: ImageArtifactDispatcher
    started_observer: ImageArtifactStartedObserver


def build_image_artifact_runtime(
    *,
    plan_loader: _PlanLoader,
    runner: _Runner = subprocess.run,
) -> ImageArtifactRuntime:
    """Compose image preparation and recovery without running either effect."""

    return ImageArtifactRuntime(
        dispatcher=ImageArtifactDispatcher(runner),
        started_observer=ImageArtifactStartedObserver(plan_loader, runner),
    )


__all__ = [
    "CANARY_DEFINITION_SHA256",
    "CANARY_COMPOSE_SHA256",
    "CANARY_DATA_SCHEMA_VERSION",
    "CANARY_IMAGE_DIGEST",
    "CANARY_IMAGE_DOWNLOAD_BYTES",
    "CANARY_IMAGE_REFERENCE",
    "CANARY_MANIFEST_SCHEMA",
    "CANARY_SERVICE_ID",
    "CANARY_VERSION",
    "ImageArtifactDispatcher",
    "ImageArtifactRuntime",
    "ImageArtifactRuntimeError",
    "ImageArtifactStartedObserver",
    "build_image_artifact_runtime",
]
