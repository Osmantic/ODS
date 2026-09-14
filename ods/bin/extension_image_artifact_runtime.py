"""Pull and verify the first plan-bound Assistant First image canary.

This module grants one narrow production effect: ``download-and-verify`` for
the exact bundled SearXNG Manifest v2 definition introduced as the first
executable canary.  It never evaluates shell text, discovers an image by tag,
or broadens the approved plan.  Docker receives only the immutable
``reference@digest`` selected by the owner-approved plan.

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
_ACTIONS = frozenset({"install", "enable", "repair", "update"})
_Runner = Callable[..., Any]
_PlanLoader = Callable[[LifecycleWorkCommand], LifecycleWorkCommand]


class ImageArtifactRuntimeError(LifecycleWorkExecutionError):
    """Stable, value-free failure after a canary command was accepted."""


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
    """Pull and locally re-open exactly one immutable canary image."""

    def __init__(self, runner: _Runner = subprocess.run) -> None:
        if not callable(runner):
            _execution_error("lifecycle-work-image-runtime-invalid")
        self._runner = runner

    def __call__(self, command: LifecycleWorkCommand) -> str:
        target = _validate_bound_command(command)
        timeout = float(command.timeout_seconds)
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
        target = _validate_bound_command(bound)
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


@dataclass(frozen=True)
class ImageArtifactRuntime:
    dispatcher: ImageArtifactDispatcher
    started_observer: ImageArtifactStartedObserver


def build_image_artifact_runtime(
    *,
    plan_loader: _PlanLoader,
    runner: _Runner = subprocess.run,
) -> ImageArtifactRuntime:
    """Compose the canary effect and observer without running either one."""

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
