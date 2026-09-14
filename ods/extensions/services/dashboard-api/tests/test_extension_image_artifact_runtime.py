from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
CATALOG_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "extensions-catalog.json"
)
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_image_artifact_runtime as image_runtime  # noqa: E402
from extension_lifecycle_plan import (  # noqa: E402
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedImage,
    PlannedOperation,
)
from extension_lifecycle_work import (  # noqa: E402
    LifecycleWorkCommand,
    LifecycleWorkValidationError,
)


TXN = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
REQUEST_HASH = "3" * 64
IMAGE_ID = "sha256:" + "4" * 64


class Runner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def completed(returncode=0, stdout=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def definition(**changes) -> PlannedDefinition:
    values = {
        "service_id": image_runtime.CANARY_SERVICE_ID,
        "service_type": "docker",
        "manifest_schema_version": image_runtime.CANARY_MANIFEST_SCHEMA,
        "version": image_runtime.CANARY_VERSION,
        "data_schema_version": image_runtime.CANARY_DATA_SCHEMA_VERSION,
        "definition_sha256": image_runtime.CANARY_DEFINITION_SHA256,
        "compose_sha256": image_runtime.CANARY_COMPOSE_SHA256,
        "definition_source": "builtin",
        "compose_file": "compose.yaml",
        "images": (
            PlannedImage(
                reference=image_runtime.CANARY_IMAGE_REFERENCE,
                digest=image_runtime.CANARY_IMAGE_DIGEST,
                download_bytes=image_runtime.CANARY_IMAGE_DOWNLOAD_BYTES,
            ),
        ),
        "builds": (),
        "canonical_document": b"{}\n",
        "host_ports": (),
        "exclusive": (),
    }
    values.update(changes)
    return PlannedDefinition(**values)


def material(*, action="install", definition_value=None, state="downloading"):
    return LifecyclePlanMaterial(
        schema=PLAN_MATERIAL_SCHEMA,
        transaction_id=TXN,
        plan_hash=PLAN_HASH,
        state=state,
        operations=(PlannedOperation(image_runtime.CANARY_SERVICE_ID, action),),
        definitions=(definition_value or definition(),),
    )


def command(*, action="install", bound=True, **changes):
    values = {
        "transaction_id": TXN,
        "plan_hash": PLAN_HASH,
        "operation_key": "download-and-verify",
        "request_hash": REQUEST_HASH,
        "service_ids": (image_runtime.CANARY_SERVICE_ID,),
        "payload": {
            "operations": [
                {
                    "serviceId": image_runtime.CANARY_SERVICE_ID,
                    "action": action,
                }
            ]
        },
        "timeout_seconds": 120,
        "plan_material": material(action=action) if bound else None,
    }
    values.update(changes)
    return LifecycleWorkCommand(**values)


def loader(plan=None):
    plan = plan or material()

    def load(value):
        return replace(value, plan_material=plan)

    return load


def test_canary_allowlist_matches_generated_catalog():
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    entries = [
        entry
        for entry in catalog["extensions"]
        if entry["id"] == image_runtime.CANARY_SERVICE_ID
    ]

    assert len(entries) == 1
    entry = entries[0]
    planning = entry["planning"]
    assert entry["manifest_schema_version"] == image_runtime.CANARY_MANIFEST_SCHEMA
    assert planning["serviceType"] == "docker"
    assert planning["version"] == image_runtime.CANARY_VERSION
    assert planning["dataSchemaVersion"] == image_runtime.CANARY_DATA_SCHEMA_VERSION
    assert planning["definitionSource"] == "builtin"
    assert planning["definitionSha256"] == image_runtime.CANARY_DEFINITION_SHA256
    assert planning["composeSha256"] == image_runtime.CANARY_COMPOSE_SHA256
    assert planning["composeFile"] == "compose.yaml"
    assert planning["artifacts"]["builds"] == []
    assert planning["artifacts"]["images"] == [
        {
            "reference": image_runtime.CANARY_IMAGE_REFERENCE,
            "digest": image_runtime.CANARY_IMAGE_DIGEST,
            "downloadBytes": image_runtime.CANARY_IMAGE_DOWNLOAD_BYTES,
        }
    ]


def test_dispatcher_pulls_exact_digest_then_reopens_local_image():
    runner = Runner([completed(), completed(stdout=IMAGE_ID + "\n")])

    evidence = image_runtime.ImageArtifactDispatcher(runner)(command())

    target = (
        f"{image_runtime.CANARY_IMAGE_REFERENCE}@{image_runtime.CANARY_IMAGE_DIGEST}"
    )
    assert runner.calls[0][0] == [
        "docker",
        "image",
        "pull",
        "--quiet",
        target,
    ]
    assert runner.calls[1][0] == [
        "docker",
        "image",
        "inspect",
        "--format",
        "{{.Id}}",
        target,
    ]
    assert len(evidence) == 64
    assert set(evidence) <= set("0123456789abcdef")
    assert all(call[1]["check"] is False for call in runner.calls)
    assert all(call[1]["stdin"] == subprocess.DEVNULL for call in runner.calls)
    assert all("shell" not in call[1] for call in runner.calls)


@pytest.mark.parametrize("action", ["install", "enable", "repair", "update"])
def test_dispatcher_accepts_only_planner_mutation_actions(action):
    runner = Runner([completed(), completed(stdout=IMAGE_ID)])
    assert (
        len(image_runtime.ImageArtifactDispatcher(runner)(command(action=action))) == 64
    )


@pytest.mark.parametrize(
    "changed_definition",
    [
        definition(service_id="other"),
        definition(manifest_schema_version="ods.services.v1"),
        definition(version="different"),
        definition(data_schema_version="different"),
        definition(definition_source="user"),
        definition(definition_sha256="sha256:" + "a" * 64),
        definition(compose_sha256="sha256:" + "c" * 64),
        definition(compose_file="other.yaml"),
        definition(images=()),
        definition(builds=(object(),)),
        definition(
            images=(
                PlannedImage(
                    reference="example.invalid/image:tag",
                    digest=image_runtime.CANARY_IMAGE_DIGEST,
                    download_bytes=image_runtime.CANARY_IMAGE_DOWNLOAD_BYTES,
                ),
            )
        ),
        definition(
            images=(
                PlannedImage(
                    reference=image_runtime.CANARY_IMAGE_REFERENCE,
                    digest="sha256:" + "b" * 64,
                    download_bytes=image_runtime.CANARY_IMAGE_DOWNLOAD_BYTES,
                ),
            )
        ),
        definition(
            images=(
                PlannedImage(
                    reference=image_runtime.CANARY_IMAGE_REFERENCE,
                    digest=image_runtime.CANARY_IMAGE_DIGEST,
                    download_bytes=1,
                ),
            )
        ),
    ],
)
def test_canary_mismatch_fails_before_subprocess(changed_definition):
    plan = material(definition_value=changed_definition)
    runner = Runner([])
    value = command(plan_material=plan)

    with pytest.raises(
        LifecycleWorkValidationError,
        match="lifecycle-work-(?:image-canary-denied|plan-mismatch)",
    ):
        image_runtime.ImageArtifactDispatcher(runner)(value)
    assert runner.calls == []


def test_non_canary_service_is_denied_before_subprocess():
    runner = Runner([])
    value = command(
        service_ids=("documents",),
        payload={"operations": [{"serviceId": "documents", "action": "install"}]},
    )
    with pytest.raises(
        LifecycleWorkValidationError, match="lifecycle-work-image-canary-denied"
    ):
        image_runtime.ImageArtifactDispatcher(runner)(value)
    assert runner.calls == []


@pytest.mark.parametrize(
    "changed_command",
    [
        command(operation_key="stage"),
        command(plan_material=material(state="staged")),
    ],
)
def test_wrong_operation_or_state_is_denied_before_subprocess(changed_command):
    runner = Runner([])
    with pytest.raises(LifecycleWorkValidationError):
        image_runtime.ImageArtifactDispatcher(runner)(changed_command)
    assert runner.calls == []


def test_pull_failure_is_value_free_and_stops_before_inspect():
    runner = Runner([completed(returncode=1, stdout="private output")])
    with pytest.raises(
        image_runtime.ImageArtifactRuntimeError,
        match="^lifecycle-work-image-command-failed$",
    ):
        image_runtime.ImageArtifactDispatcher(runner)(command())
    assert len(runner.calls) == 1


def test_timeout_is_value_free():
    runner = Runner(
        [subprocess.TimeoutExpired(["docker", "image", "pull"], timeout=120)]
    )
    with pytest.raises(
        image_runtime.ImageArtifactRuntimeError,
        match="^lifecycle-work-image-command-timeout$",
    ):
        image_runtime.ImageArtifactDispatcher(runner)(command())


def test_successful_inspect_with_invalid_image_id_fails_closed():
    runner = Runner([completed(), completed(stdout="not-an-image-id\n")])
    with pytest.raises(
        image_runtime.ImageArtifactRuntimeError,
        match="^lifecycle-work-image-inspect-invalid$",
    ):
        image_runtime.ImageArtifactDispatcher(runner)(command())


def test_started_observer_recovers_same_evidence_without_pull():
    dispatch_runner = Runner([completed(), completed(stdout=IMAGE_ID)])
    expected = image_runtime.ImageArtifactDispatcher(dispatch_runner)(command())
    observe_runner = Runner([completed(stdout=IMAGE_ID)])
    observer = image_runtime.ImageArtifactStartedObserver(loader(), observe_runner)

    observed = observer(command(bound=False))

    assert observed.state == "completed"
    assert observed.evidence_hash == expected
    assert len(observe_runner.calls) == 1
    assert observe_runner.calls[0][0][2] == "inspect"


def test_started_observer_reports_missing_without_pull():
    runner = Runner([completed(returncode=1)])
    observed = image_runtime.ImageArtifactStartedObserver(loader(), runner)(
        command(bound=False)
    )
    assert observed.state == "missing"
    assert observed.evidence_hash is None
    assert len(runner.calls) == 1


def test_started_observer_rejects_loader_binding_change():
    def changed(value):
        return replace(value, plan_hash="f" * 64, plan_material=material())

    runner = Runner([])
    observer = image_runtime.ImageArtifactStartedObserver(changed, runner)
    with pytest.raises(
        LifecycleWorkValidationError, match="lifecycle-work-plan-mismatch"
    ):
        observer(command(bound=False))
    assert runner.calls == []


def test_runtime_construction_is_inert():
    runner = Runner([])
    runtime = image_runtime.build_image_artifact_runtime(
        plan_loader=loader(), runner=runner
    )
    assert isinstance(runtime.dispatcher, image_runtime.ImageArtifactDispatcher)
    assert isinstance(
        runtime.started_observer, image_runtime.ImageArtifactStartedObserver
    )
    assert runner.calls == []
