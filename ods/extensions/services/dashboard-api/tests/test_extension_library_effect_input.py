from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest


BIN = Path(__file__).resolve().parents[4] / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

import extension_library_effect_input as gate  # noqa: E402
from extension_artifact_stage_store import (  # noqa: E402
    StagedArtifactBatch,
    StagedArtifactFile,
    StagedDefinitionArtifacts,
)
from extension_document_digest import canonical_document_sha256  # noqa: E402
from extension_library_tree_digest import digest_extension_tree  # noqa: E402
from extension_lifecycle_plan import (  # noqa: E402
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedOperation,
)
from extension_lifecycle_work import LifecycleWorkCommand  # noqa: E402


pytestmark = pytest.mark.skipif(
    os.name != "posix"
    or not hasattr(os, "O_DIRECTORY")
    or not hasattr(os, "O_NOFOLLOW"),
    reason="requires Linux descriptor snapshot semantics",
)


@pytest.fixture(autouse=True)
def private_tree_umask():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def _file(path: str, content: bytes) -> StagedArtifactFile:
    return StagedArtifactFile(
        relative_path=path,
        content=content,
        semantic_sha256=canonical_document_sha256(content),
        raw_sha256="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


def _fixture(tmp_path: Path):
    library = tmp_path / "library"
    service = library / "demo"
    (service / "hooks").mkdir(parents=True)
    manifest = b"schema_version: ods.services.v2\nservice:\n  id: demo\n"
    compose = b"services:\n  demo:\n    image: example/demo@sha256:" + b"1" * 64 + b"\n"
    (service / "manifest.yaml").write_bytes(manifest)
    (service / "compose.yaml").write_bytes(compose)
    hook = service / "hooks" / "setup.sh"
    hook.write_bytes(b"#!/bin/sh\nexit 0\n")
    hook.chmod(0o700)
    (service / "README.md").write_bytes(b"approved supporting payload\n")
    definition = PlannedDefinition(
        service_id="demo",
        service_type="docker",
        manifest_schema_version="ods.services.v2",
        version="1.0.0",
        data_schema_version="1",
        definition_sha256=canonical_document_sha256(manifest),
        compose_sha256=canonical_document_sha256(compose),
        definition_source="library",
        compose_file="compose.yaml",
        images=(),
        builds=(),
        canonical_document=b"ignored\n",
        source_tree_sha256=digest_extension_tree(service),
    )
    operation = PlannedOperation("demo", "install")
    material = LifecyclePlanMaterial(
        schema=PLAN_MATERIAL_SCHEMA,
        transaction_id="txn-" + "1" * 24,
        plan_hash="2" * 64,
        state="applying",
        operations=(operation,),
        definitions=(definition,),
        attested_approval=True,
    )
    command = LifecycleWorkCommand(
        transaction_id=material.transaction_id,
        plan_hash=material.plan_hash,
        operation_key="apply:demo",
        request_hash="3" * 64,
        service_ids=("demo",),
        payload={"operation": {"serviceId": "demo", "action": "install"}},
        timeout_seconds=60,
        plan_material=material,
    )
    staged = StagedArtifactBatch(
        transaction_id=command.transaction_id,
        plan_hash=command.plan_hash,
        service_ids=("demo",),
        definitions=(
            StagedDefinitionArtifacts(
                service_id="demo",
                definition_source="library",
                manifest=_file("manifest.yaml", manifest),
                compose=_file("compose.yaml", compose),
            ),
        ),
        bundle_sha256="4" * 64,
        duplicate=False,
    )
    return library, service, command, staged


def test_returns_every_approved_byte_for_future_effect(tmp_path):
    library, _service, command, staged = _fixture(tmp_path)
    result = gate.verify_plan_bound_library_payload(command, staged, library)
    assert result.transaction_id == command.transaction_id
    assert result.plan_hash == command.plan_hash
    assert result.service_id == "demo"
    assert (
        result.payload.digest == command.plan_material.definitions[0].source_tree_sha256
    )
    assert {item.relative_path for item in result.payload.files} == {
        "README.md",
        "compose.yaml",
        "hooks/setup.sh",
        "manifest.yaml",
    }


def test_refuses_supporting_file_drift_even_when_stage_still_matches(tmp_path):
    library, service, command, staged = _fixture(tmp_path)
    (service / "hooks" / "setup.sh").write_bytes(b"#!/bin/sh\nmalicious\n")
    with pytest.raises(
        gate.LibraryEffectInputError, match="library-effect-source-mismatch"
    ):
        gate.verify_plan_bound_library_payload(command, staged, library)


def test_refuses_stage_bytes_that_do_not_match_the_approved_live_snapshot(tmp_path):
    library, _service, command, staged = _fixture(tmp_path)
    altered = replace(
        staged.definitions[0],
        compose=_file("compose.yaml", b"services: {}\n"),
    )
    with pytest.raises(
        gate.LibraryEffectInputError, match="library-effect-stage-mismatch"
    ):
        gate.verify_plan_bound_library_payload(
            command, replace(staged, definitions=(altered,)), library
        )


def test_refuses_v1_warning_only_definition_before_source_read(tmp_path, monkeypatch):
    library, _service, command, staged = _fixture(tmp_path)
    legacy = replace(
        command.plan_material.definitions[0],
        manifest_schema_version="ods.services.v1",
        source_tree_sha256=None,
    )
    command = replace(
        command,
        plan_material=replace(command.plan_material, definitions=(legacy,)),
    )
    monkeypatch.setattr(
        gate,
        "snapshot_extension_tree",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not read")),
    )
    with pytest.raises(
        gate.LibraryEffectInputError, match="library-effect-definition-unsupported"
    ):
        gate.verify_plan_bound_library_payload(command, staged, library)


def test_refuses_unattested_or_wrong_state_plan_before_source_read(
    tmp_path, monkeypatch
):
    library, _service, command, staged = _fixture(tmp_path)
    monkeypatch.setattr(
        gate,
        "snapshot_extension_tree",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not read")),
    )
    for material in (
        replace(command.plan_material, attested_approval=False),
        replace(command.plan_material, state="staged"),
    ):
        with pytest.raises(
            gate.LibraryEffectInputError, match="library-effect-plan-mismatch"
        ):
            gate.verify_plan_bound_library_payload(
                replace(command, plan_material=material), staged, library
            )


def test_refuses_symlinked_supporting_payload(tmp_path):
    library, service, command, staged = _fixture(tmp_path)
    source = service / "README.md"
    source.unlink()
    try:
        source.symlink_to(service / "manifest.yaml")
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(
        gate.LibraryEffectInputError, match="library-effect-source-invalid"
    ):
        gate.verify_plan_bound_library_payload(command, staged, library)
