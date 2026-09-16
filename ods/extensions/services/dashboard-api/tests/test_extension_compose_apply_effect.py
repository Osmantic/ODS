"""Focused dormant SearXNG Compose-effect tests; never invoke Docker."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

ODS = Path(__file__).resolve().parents[4]
BIN = ODS / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

import extension_application_identity as application_identity
import extension_application_observation_adapter as observation_adapter
import extension_compose_apply_effect as effect
import extension_configuration_effect_runtime as configuration_runtime
import extension_document_digest as document_digest
from extension_artifact_stage_store import (
    StagedArtifactBatch,
    StagedArtifactFile,
    StagedDefinitionArtifacts,
)
from extension_configuration_effect_runtime import BoundConfiguration
from extension_image_artifact_runtime import (
    CANARY_COMPOSE_SHA256,
    CANARY_DEFINITION_SHA256,
    CANARY_IMAGE_DIGEST,
    CANARY_IMAGE_DOWNLOAD_BYTES,
    CANARY_IMAGE_REFERENCE,
)
from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedImage,
    PlannedOperation,
)
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkValidationError,
)

SUPPORTED = (
    os.name == "posix"
    and all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW"))
    and all(
        fn in os.supports_dir_fd
        for fn in (os.open, os.stat, os.mkdir, os.link, os.unlink)
    )
    and os.stat in os.supports_follow_symlinks
)
pytestmark = pytest.mark.skipif(not SUPPORTED, reason="requires Linux dirfd custody")

TXN = "txn-" + "1" * 24
PLAN = "2" * 64
REQUEST = "3" * 64
SCHEMA = hashlib.sha256(
    (
        json.dumps(
            {
                "schema": configuration_runtime.CONFIGURATION_SCHEMA,
                "fields": configuration_runtime.CANARY_CONFIGURATION,
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
).hexdigest()
REFERENCE = "secret-v1-" + "5" * 48
SECRET = "sensitive-canary-secret-value"
SERVICE = ODS / "extensions" / "services" / "searxng"


def _staged_file(name: str) -> StagedArtifactFile:
    content = (SERVICE / name).read_bytes()
    return StagedArtifactFile(
        relative_path=name,
        content=content,
        semantic_sha256=document_digest.canonical_document_sha256(content),
        raw_sha256="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


def _inputs():
    manifest = _staged_file("manifest.yaml")
    compose = _staged_file("compose.yaml")
    assert manifest.semantic_sha256 == CANARY_DEFINITION_SHA256
    assert compose.semantic_sha256 == CANARY_COMPOSE_SHA256
    definition = PlannedDefinition(
        service_id="searxng",
        service_type="docker",
        manifest_schema_version="ods.services.v2",
        version="2026.3.8",
        data_schema_version="1",
        definition_sha256=CANARY_DEFINITION_SHA256,
        compose_sha256=CANARY_COMPOSE_SHA256,
        definition_source="builtin",
        compose_file="compose.yaml",
        images=(
            PlannedImage(
                reference=CANARY_IMAGE_REFERENCE,
                digest=CANARY_IMAGE_DIGEST,
                download_bytes=CANARY_IMAGE_DOWNLOAD_BYTES,
            ),
        ),
        builds=(),
        canonical_document=manifest.content,
        host_ports=(),
        exclusive=(),
    )
    material = LifecyclePlanMaterial(
        schema=PLAN_MATERIAL_SCHEMA,
        transaction_id=TXN,
        plan_hash=PLAN,
        state="applying",
        operations=(PlannedOperation("searxng", "install"),),
        definitions=(definition,),
    )
    command = LifecycleWorkCommand(
        transaction_id=TXN,
        plan_hash=PLAN,
        operation_key="apply:searxng",
        request_hash=REQUEST,
        service_ids=("searxng",),
        payload={"operation": {"serviceId": "searxng", "action": "install"}},
        timeout_seconds=60,
        plan_material=material,
    )
    staged = StagedArtifactBatch(
        transaction_id=TXN,
        plan_hash=PLAN,
        service_ids=("searxng",),
        definitions=(
            StagedDefinitionArtifacts("searxng", "builtin", manifest, compose),
        ),
        bundle_sha256="sha256:" + "6" * 64,
        duplicate=False,
    )
    configuration = BoundConfiguration(SCHEMA, 9988, False, REFERENCE)
    identity = application_identity.produce_application_identity(command)
    return command, staged, configuration, identity


class FakeSecrets:
    def __init__(self, value=SECRET):
        self.value = value
        self.requests = []

    def invoke_with_secrets(self, request, consumer):
        self.requests.append(request)
        consumer({"SEARXNG_SECRET": self.value})
        return {"configured": True}


@pytest.fixture()
def install(tmp_path):
    root = tmp_path / "install"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def _active(install):
    return install / ".ods-assistant-first" / "applications" / "searxng"


def test_exact_files_fixed_argv_and_secret_only_in_temporary_runner_env(install):
    command, staged, config, identity = _inputs()
    calls = []

    def runner(argv, environment, timeout):
        calls.append((argv, dict(environment), timeout))
        return True

    secrets = FakeSecrets()
    result = effect.ComposeApplyEffect(install, runner).apply(
        command, staged, config, identity, secrets
    )
    active = _active(install)
    assert result.outcome == "materialized"
    assert result.identity_sha256 == identity.identity_sha256
    assert result.definition_sha256 == CANARY_DEFINITION_SHA256
    assert result.compose_sha256 == CANARY_COMPOSE_SHA256
    assert (active / "manifest.yaml").read_bytes() == staged.definitions[
        0
    ].manifest.content
    metadata = (active / "configuration.json").read_bytes()
    assert result.config_sha256 == document_digest.canonical_document_sha256(metadata)
    assert json.loads(metadata) == {
        "identitySha256": identity.identity_sha256,
        "planHash": identity.plan_sha256,
        "port": 9988,
        "schema": "ods.assistant-first.active-configuration.v1",
        "schemaHash": SCHEMA,
        "serviceId": "searxng",
        "transactionId": TXN,
        "usedDefaultPort": False,
    }
    assert REFERENCE.encode() not in metadata
    assert SECRET.encode() not in metadata
    assert (active / "compose.yaml").read_bytes() == staged.definitions[
        0
    ].compose.content
    override = (active / "compose.override.yaml").read_bytes()
    assert b"x-ods-bound-port: 9988" in override
    assert SECRET.encode() not in override
    assert override.count(b"io.osmantic.ods.extension.") == 9
    service_override = yaml.safe_load(override)["services"]["searxng"]
    assert service_override["labels"] == application_identity.identity_labels(identity)
    assert service_override["x-ods-bound-port"] == 9988
    assert "ports" not in service_override
    assert all(
        stat.S_IMODE((active / name).stat().st_mode) == 0o600
        for name in (
            "manifest.yaml",
            "configuration.json",
            "compose.yaml",
            "compose.override.yaml",
        )
    )
    assert calls == [
        (
            (
                "docker",
                "compose",
                "--project-directory",
                str(install),
                "-f",
                str(active / "compose.yaml"),
                "-f",
                str(active / "compose.override.yaml"),
                "up",
                "-d",
                "--no-build",
                "--no-deps",
                "searxng",
            ),
            {
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "BIND_ADDRESS": "127.0.0.1",
                "SEARXNG_PORT": "9988",
                "SEARXNG_SECRET": SECRET,
            },
            60,
        )
    ]
    assert secrets.requests[0]["expectedSecretKeys"] == ["SEARXNG_SECRET"]
    assert SECRET not in repr(result)
    assert SECRET not in repr(secrets.requests)


def test_replay_runs_idempotent_compose_again_without_replacing_files(install):
    command, staged, config, identity = _inputs()
    calls = []
    runner = lambda argv, env, timeout: calls.append(argv) or True
    action = effect.ComposeApplyEffect(install, runner)
    first = action.apply(command, staged, config, identity, FakeSecrets())
    inodes = tuple(
        (_active(install) / name).stat().st_ino
        for name in (
            "manifest.yaml",
            "configuration.json",
            "compose.yaml",
            "compose.override.yaml",
        )
    )
    second = action.apply(command, staged, config, identity, FakeSecrets())
    assert first.outcome == "materialized"
    assert second.outcome == "replayed"
    assert len(calls) == 2  # file replay is not installed-state observation
    assert inodes == tuple(
        (_active(install) / name).stat().st_ino
        for name in (
            "manifest.yaml",
            "configuration.json",
            "compose.yaml",
            "compose.override.yaml",
        )
    )


def test_published_files_supply_current_observer_digests(install):
    command, staged, config, identity = _inputs()
    result = effect.ComposeApplyEffect(install, lambda *args: True).apply(
        command, staged, config, identity, FakeSecrets()
    )
    assert observation_adapter._current_files(install, "searxng") == (
        result.definition_sha256,
        result.compose_sha256,
        result.config_sha256,
    )


def test_concurrent_retries_serialize_entire_compose_effect(install):
    command, staged, config, identity = _inputs()
    counter_lock = threading.Lock()
    active_count = 0
    maximum = 0

    def runner(*args):
        nonlocal active_count, maximum
        with counter_lock:
            active_count += 1
            maximum = max(maximum, active_count)
        time.sleep(0.1)
        with counter_lock:
            active_count -= 1
        return True

    action = effect.ComposeApplyEffect(install, runner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(action.apply, command, staged, config, identity, FakeSecrets())
            for _ in range(2)
        ]
        results = [future.result() for future in futures]
    assert maximum == 1
    assert {result.outcome for result in results} == {"materialized", "replayed"}
    lockfile = _active(install) / ".apply.lock"
    assert lockfile.is_file()
    assert stat.S_IMODE(lockfile.stat().st_mode) == 0o600


def test_runner_failure_leaves_partial_files_for_explicit_recovery(install):
    command, staged, config, identity = _inputs()
    action = effect.ComposeApplyEffect(install, lambda *args: False)
    with pytest.raises(effect.ComposeApplyUncertainEffect) as caught:
        action.apply(command, staged, config, identity, FakeSecrets())
    assert str(caught.value) == "lifecycle-work-compose-runner-failed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert (_active(install) / "manifest.yaml").is_file()
    assert (_active(install) / "configuration.json").is_file()
    assert (_active(install) / "compose.yaml").is_file()
    assert (_active(install) / "compose.override.yaml").is_file()


@pytest.mark.parametrize(
    "change", ["command", "stage", "identity", "configuration", "schema", "image"]
)
def test_invalid_inputs_do_not_invoke_secret_or_runner(install, change):
    command, staged, config, identity = _inputs()
    if change == "command":
        command = replace(command, operation_key="apply:documents")
    elif change == "stage":
        staged = replace(staged, plan_hash="9" * 64)
    elif change == "identity":
        identity = replace(identity, plan_sha256="9" * 64)
    elif change == "configuration":
        config = replace(config, port=True)
    elif change == "schema":
        config = replace(config, schema_hash="9" * 64)
    else:
        definition = command.plan_material.definitions[0]
        image = replace(definition.images[0], digest="sha256:" + "9" * 64)
        material = replace(
            command.plan_material,
            definitions=(replace(definition, images=(image,)),),
        )
        command = replace(command, plan_material=material)
    called = []
    secret = FakeSecrets()
    with pytest.raises(LifecycleWorkValidationError):
        effect.ComposeApplyEffect(
            install, lambda *args: called.append(True) or True
        ).apply(command, staged, config, identity, secret)
    assert called == []
    assert secret.requests == []


@pytest.mark.parametrize(
    "name",
    ("manifest.yaml", "configuration.json", "compose.yaml", "compose.override.yaml"),
)
def test_symlink_or_hardlink_collision_fails_closed(install, tmp_path, name):
    command, staged, config, identity = _inputs()
    active = _active(install)
    active.mkdir(parents=True, mode=0o700)
    for directory in (active.parent.parent, active.parent, active):
        directory.chmod(0o700)
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"foreign")
    for linked in ("symlink", "hardlink"):
        target = active / name
        if linked == "symlink":
            target.symlink_to(foreign)
        else:
            os.link(foreign, target)
        with pytest.raises(effect.ComposeApplyEffectError):
            effect.ComposeApplyEffect(install, lambda *args: True).apply(
                command, staged, config, identity, FakeSecrets()
            )
        target.unlink()


def test_preexisting_config_drift_refuses_before_any_publication(install):
    command, staged, config, identity = _inputs()
    active = _active(install)
    active.mkdir(parents=True, mode=0o700)
    for directory in (active.parent.parent, active.parent, active):
        directory.chmod(0o700)
    (active / "configuration.json").write_bytes(b"{}\n")
    (active / "configuration.json").chmod(0o600)
    calls = []
    with pytest.raises(effect.ComposeApplyEffectError) as caught:
        effect.ComposeApplyEffect(
            install, lambda *args: calls.append(True) or True
        ).apply(command, staged, config, identity, FakeSecrets())
    assert str(caught.value) == "lifecycle-work-compose-file-drift"
    assert calls == []
    assert not (active / "manifest.yaml").exists()
    assert not (active / "compose.yaml").exists()


def test_lockfile_collision_or_unsafe_mode_fails_before_runner(install, tmp_path):
    command, staged, config, identity = _inputs()
    active = _active(install)
    active.mkdir(parents=True, mode=0o700)
    for directory in (active.parent.parent, active.parent, active):
        directory.chmod(0o700)
    foreign = tmp_path / "foreign-lock"
    foreign.write_bytes(b"x")
    lockfile = active / ".apply.lock"
    lockfile.symlink_to(foreign)
    calls = []
    action = effect.ComposeApplyEffect(
        install, lambda *args: calls.append(True) or True
    )
    with pytest.raises(effect.ComposeApplyEffectError):
        action.apply(command, staged, config, identity, FakeSecrets())
    lockfile.unlink()
    lockfile.write_bytes(b"")
    lockfile.chmod(0o644)
    with pytest.raises(effect.ComposeApplyEffectError):
        action.apply(command, staged, config, identity, FakeSecrets())
    assert calls == []


def test_foreign_active_entry_fails_closed_before_runner(install):
    command, staged, config, identity = _inputs()
    active = _active(install)
    active.mkdir(parents=True, mode=0o700)
    for directory in (active.parent.parent, active.parent, active):
        directory.chmod(0o700)
    (active / "foreign.compose.yaml").write_bytes(b"foreign")
    calls = []
    with pytest.raises(effect.ComposeApplyEffectError):
        effect.ComposeApplyEffect(
            install, lambda *args: calls.append(True) or True
        ).apply(command, staged, config, identity, FakeSecrets())
    assert calls == []


def test_secret_callback_error_never_escapes_raw_value(install):
    command, staged, config, identity = _inputs()

    def failing(*args):
        raise RuntimeError(SECRET)

    with pytest.raises(effect.ComposeApplyUncertainEffect) as caught:
        effect.ComposeApplyEffect(install, failing).apply(
            command, staged, config, identity, FakeSecrets()
        )
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_production_host_agent_does_not_import_dormant_effect():
    source = (ODS / "bin" / "ods-host-agent.py").read_text(encoding="utf-8")
    assert "extension_compose_apply_effect" not in source
