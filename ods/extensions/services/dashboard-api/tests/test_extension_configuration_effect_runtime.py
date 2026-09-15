from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from dataclasses import replace
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
CATALOG_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "extensions-catalog.json"
)
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import assistant_first_secret_store as secret_store
import extension_configuration_effect_runtime as configuration_runtime
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
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and all(
        function in os.supports_dir_fd
        for function in (os.open, os.stat, os.mkdir, os.unlink, os.rename)
    )
    and os.stat in os.supports_follow_symlinks
)
pytestmark = pytest.mark.skipif(
    not SUPPORTED, reason="requires Linux descriptor-relative filesystem semantics"
)

TXN = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
REQUEST_HASH = "3" * 64
SCHEMA_HASH = hashlib.sha256(
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
SECRET_REFERENCE = "secret-v1-" + "4" * 48
SECRET_VALUE = "5" * 64


def canonical_document(*, configuration=None) -> bytes:
    value = {
        "configuration": (
            configuration
            if configuration is not None
            else configuration_runtime.CANARY_CONFIGURATION
        )
    }
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def definition(**changes) -> PlannedDefinition:
    values = {
        "service_id": configuration_runtime.CANARY_SERVICE_ID,
        "service_type": "docker",
        "manifest_schema_version": configuration_runtime.CANARY_MANIFEST_SCHEMA,
        "version": configuration_runtime.CANARY_VERSION,
        "data_schema_version": configuration_runtime.CANARY_DATA_SCHEMA_VERSION,
        "definition_sha256": configuration_runtime.CANARY_DEFINITION_SHA256,
        "compose_sha256": configuration_runtime.CANARY_COMPOSE_SHA256,
        "definition_source": "builtin",
        "compose_file": "compose.yaml",
        "images": (
            PlannedImage(
                reference=configuration_runtime.CANARY_IMAGE_REFERENCE,
                digest=configuration_runtime.CANARY_IMAGE_DIGEST,
                download_bytes=configuration_runtime.CANARY_IMAGE_DOWNLOAD_BYTES,
            ),
        ),
        "builds": (),
        "canonical_document": canonical_document(),
        "host_ports": (),
        "exclusive": (),
    }
    values.update(changes)
    return PlannedDefinition(**values)


def material(*, definition_value=None, state="configuring") -> LifecyclePlanMaterial:
    return LifecyclePlanMaterial(
        schema=PLAN_MATERIAL_SCHEMA,
        transaction_id=TXN,
        plan_hash=PLAN_HASH,
        state=state,
        operations=(
            PlannedOperation(configuration_runtime.CANARY_SERVICE_ID, "install"),
        ),
        definitions=(definition_value or definition(),),
    )


def command(*, bound=True, material_value=None, **changes) -> LifecycleWorkCommand:
    values = {
        "transaction_id": TXN,
        "plan_hash": PLAN_HASH,
        "operation_key": "configure",
        "request_hash": REQUEST_HASH,
        "service_ids": (configuration_runtime.CANARY_SERVICE_ID,),
        "payload": {"serviceIds": [configuration_runtime.CANARY_SERVICE_ID]},
        "timeout_seconds": 600,
        "plan_material": (
            material_value or material() if bound else None
        ),
    }
    values.update(changes)
    return LifecycleWorkCommand(**values)


def configuration_record(*, port=None, **changes):
    if port is None:
        values = {}
        present = []
        defaults = ["SEARXNG_PORT"]
    else:
        values = {"SEARXNG_PORT": port}
        present = ["SEARXNG_PORT"]
        defaults = []
    record = {
        "actor": "owner",
        "appliedDefaultKeys": defaults,
        "configuredAt": "2026-09-14T00:00:00Z",
        "idempotencyKey": "6" * 64,
        "planHash": PLAN_HASH,
        "presentConfigKeys": present,
        "presentSecretKeys": ["SEARXNG_SECRET"],
        "schema": configuration_runtime.TRANSACTION_CONFIGURATION_SCHEMA,
        "schemaHash": SCHEMA_HASH,
        "secretReference": SECRET_REFERENCE,
        "transactionId": TXN,
        "values": values,
    }
    record.update(changes)
    return record


def transaction(*, port=None, record_changes=None, **changes):
    value = {
        "transactionId": TXN,
        "state": "configuring",
        "envelope": {"planHash": PLAN_HASH},
        "approval": {"planHash": PLAN_HASH},
        "configuration": configuration_record(
            port=port, **(record_changes or {})
        ),
    }
    value.update(changes)
    return value


def status_response(**changes):
    value = {
        "schema": secret_store.STATUS_SCHEMA,
        "transactionId": TXN,
        "planHash": PLAN_HASH,
        "schemaHash": SCHEMA_HASH,
        "reference": SECRET_REFERENCE,
        "configured": True,
        "presentSecretKeys": ["SEARXNG_SECRET"],
    }
    value.update(changes)
    return value


@pytest.fixture()
def roots(tmp_path):
    install = tmp_path / "install"
    config = install / "config"
    install.mkdir(mode=0o700)
    config.mkdir(mode=0o700)
    install.chmod(0o700)
    config.chmod(0o700)
    return install, config


def build(
    roots,
    *,
    transaction_value=None,
    status_value=None,
    plan_loader=None,
    status_calls=None,
):
    install, _config = roots

    def load_plan(value):
        return replace(value, plan_material=material())

    def load_transaction(_transaction_id):
        return transaction_value or transaction()

    def load_status(payload):
        if status_calls is not None:
            status_calls.append(payload)
        return status_value or status_response()

    return configuration_runtime.build_configuration_effect_runtime(
        install_dir=install,
        plan_loader=plan_loader or load_plan,
        transaction_loader=load_transaction,
        secret_status=load_status,
    )


def test_canary_configuration_allowlist_matches_generated_catalog():
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    entries = [
        item
        for item in catalog["extensions"]
        if item["id"] == configuration_runtime.CANARY_SERVICE_ID
    ]
    assert len(entries) == 1
    assert entries[0]["planning"]["configuration"] == (
        configuration_runtime.CANARY_CONFIGURATION
    )


@pytest.mark.parametrize("port", [None, 9999])
def test_publish_is_secret_free_deterministic_and_replay_safe(roots, port):
    status_calls = []
    runtime = build(
        roots,
        transaction_value=transaction(port=port),
        status_calls=status_calls,
    )

    first = runtime.dispatcher(command())
    settings = roots[1] / "searxng" / "settings.yml"
    first_identity = (settings.stat().st_dev, settings.stat().st_ino)
    second = runtime.dispatcher(command())

    assert len(first) == 64
    assert first == second
    assert (settings.stat().st_dev, settings.stat().st_ino) == first_identity
    assert settings.read_bytes() == configuration_runtime.CANARY_SETTINGS_BYTES
    assert stat.S_IMODE(settings.stat().st_mode) == 0o644
    rendered = settings.read_text(encoding="utf-8")
    for forbidden in (
        SECRET_VALUE,
        SECRET_REFERENCE,
        "SEARXNG_SECRET",
        "secret_key",
    ):
        assert forbidden not in rendered
        assert forbidden not in first
    assert status_calls == [
        {
            "schema": secret_store.STATUS_REQUEST_SCHEMA,
            "transactionId": TXN,
            "planHash": PLAN_HASH,
            "schemaHash": SCHEMA_HASH,
            "reference": SECRET_REFERENCE,
        },
        {
            "schema": secret_store.STATUS_REQUEST_SCHEMA,
            "transactionId": TXN,
            "planHash": PLAN_HASH,
            "schemaHash": SCHEMA_HASH,
            "reference": SECRET_REFERENCE,
        },
    ]


def test_exact_content_with_private_mode_is_atomically_republished(roots):
    service = roots[1] / "searxng"
    service.mkdir(mode=0o700)
    service.chmod(0o700)
    settings = service / "settings.yml"
    settings.write_bytes(configuration_runtime.CANARY_SETTINGS_BYTES)
    settings.chmod(0o600)
    original_identity = (settings.stat().st_dev, settings.stat().st_ino)

    build(roots).dispatcher(command())

    assert settings.read_bytes() == configuration_runtime.CANARY_SETTINGS_BYTES
    assert stat.S_IMODE(settings.stat().st_mode) == 0o644
    assert (settings.stat().st_dev, settings.stat().st_ino) != original_identity


def test_started_observer_recovers_published_effect_without_redispatch(roots):
    runtime = build(roots)
    unbound = command(bound=False)
    assert runtime.started_observer(unbound).state == "missing"

    expected = runtime.dispatcher(command())
    settings = roots[1] / "searxng" / "settings.yml"
    identity = (settings.stat().st_dev, settings.stat().st_ino)
    observed = runtime.started_observer(unbound)

    assert observed.state == "completed"
    assert observed.evidence_hash == expected
    assert (settings.stat().st_dev, settings.stat().st_ino) == identity


def test_observer_recovers_when_publication_succeeds_before_error(
    roots, monkeypatch
):
    runtime = build(roots)
    original = configuration_runtime.os.rename

    def publish_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("private-post-publication-detail")

    with monkeypatch.context() as patch:
        patch.setattr(configuration_runtime.os, "rename", publish_then_fail)
        with pytest.raises(
            configuration_runtime.ConfigurationEffectRuntimeError
        ) as error:
            runtime.dispatcher(command())
        assert error.value.code == "lifecycle-work-configuration-write-failed"
        assert "private-post-publication-detail" not in str(error.value)

    observed = runtime.started_observer(command(bound=False))
    assert observed.state == "completed"
    assert observed.evidence_hash is not None


@pytest.mark.parametrize(
    "changed",
    [
        {"operation_key": "verify"},
        {"service_ids": ("documents",)},
        {"payload": {"serviceIds": ["documents"]}},
        {"material_value": material(state="applying")},
        {
            "material_value": material(
                definition_value=definition(
                    canonical_document=canonical_document(configuration=[])
                )
            )
        },
    ],
)
def test_misbound_commands_fail_before_publication(roots, changed):
    runtime = build(roots)
    with pytest.raises(LifecycleWorkValidationError):
        runtime.dispatcher(command(**changed))
    assert not (roots[1] / "searxng").exists()


@pytest.mark.parametrize(
    "transaction_value,status_value",
    [
        (transaction(state="applying"), None),
        (
            transaction(record_changes={"planHash": "7" * 64}),
            None,
        ),
        (
            transaction(record_changes={"presentSecretKeys": []}),
            None,
        ),
        (
            None,
            status_response(reference="secret-v1-" + "8" * 48),
        ),
        (
            None,
            status_response(presentSecretKeys=[]),
        ),
        (
            None,
            status_response(secretValues={"SEARXNG_SECRET": SECRET_VALUE}),
        ),
    ],
)
def test_transaction_and_secret_mismatches_fail_before_publication(
    roots, transaction_value, status_value
):
    runtime = build(
        roots,
        transaction_value=transaction_value,
        status_value=status_value,
    )
    with pytest.raises(
        (
            LifecycleWorkValidationError,
            configuration_runtime.ConfigurationEffectRuntimeError,
        )
    ):
        runtime.dispatcher(command())
    assert not (roots[1] / "searxng").exists()


@pytest.mark.parametrize("port", [True, 0, 65536])
def test_invalid_stored_port_fails_before_publication(roots, port):
    runtime = build(roots, transaction_value=transaction(port=port))
    with pytest.raises(LifecycleWorkValidationError) as error:
        runtime.dispatcher(command())
    assert error.value.code == "lifecycle-work-configuration-mismatch"
    assert not (roots[1] / "searxng").exists()


def test_symlinked_configuration_directory_is_rejected(roots, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (roots[1] / "searxng").symlink_to(outside, target_is_directory=True)

    with pytest.raises(configuration_runtime.ConfigurationEffectRuntimeError):
        build(roots).dispatcher(command())
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("kind", ["hardlink", "fifo", "unsafe-mode"])
def test_hostile_existing_settings_are_rejected_without_replacement(
    roots, tmp_path, kind
):
    service = roots[1] / "searxng"
    service.mkdir(mode=0o700)
    service.chmod(0o700)
    settings = service / "settings.yml"
    if kind == "hardlink":
        outside = tmp_path / "outside-settings"
        outside.write_bytes(b"outside")
        os.link(outside, settings)
    elif kind == "fifo":
        os.mkfifo(settings, mode=0o600)
    else:
        settings.write_bytes(b"unsafe")
        settings.chmod(0o666)

    before = os.lstat(settings)
    with pytest.raises(configuration_runtime.ConfigurationEffectRuntimeError):
        build(roots).dispatcher(command())
    after = os.lstat(settings)
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        before.st_dev,
        before.st_ino,
        before.st_mode,
    )


def test_unsafe_directory_modes_and_foreign_ownership_fail_closed(
    roots, monkeypatch
):
    service = roots[1] / "searxng"
    service.mkdir(mode=0o777)
    service.chmod(0o777)
    with pytest.raises(configuration_runtime.ConfigurationEffectRuntimeError):
        build(roots).dispatcher(command())
    assert list(service.iterdir()) == []

    service.chmod(0o700)
    runtime = build(roots)
    monkeypatch.setattr(
        configuration_runtime.os,
        "geteuid",
        lambda: os.getuid() + 1,
    )
    with pytest.raises(configuration_runtime.ConfigurationEffectRuntimeError):
        runtime.dispatcher(command())
    assert list(service.iterdir()) == []


def test_construction_is_inert_and_requires_existing_config_root(
    roots, tmp_path
):
    runtime = build(roots)
    assert runtime.store.matches() is False
    assert list(roots[1].iterdir()) == []

    missing_config_install = tmp_path / "missing-config"
    missing_config_install.mkdir(mode=0o700)
    missing_config_install.chmod(0o700)
    with pytest.raises(configuration_runtime.ConfigurationEffectRuntimeError) as error:
        configuration_runtime.build_configuration_effect_runtime(
            install_dir=missing_config_install,
            plan_loader=lambda value: value,
            transaction_loader=lambda _transaction_id: transaction(),
            secret_status=lambda _payload: status_response(),
        )
    assert error.value.code == "lifecycle-work-configuration-path-unavailable"
    assert list(missing_config_install.iterdir()) == []


def test_observer_rejects_reloaded_plan_drift(roots):
    def drifting_loader(value):
        return replace(value, plan_hash="9" * 64, plan_material=material())

    runtime = build(roots, plan_loader=drifting_loader)
    with pytest.raises(LifecycleWorkValidationError) as error:
        runtime.started_observer(command(bound=False))
    assert error.value.code == "lifecycle-work-plan-mismatch"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
